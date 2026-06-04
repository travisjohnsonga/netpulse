"""SonicWall SonicOS REST API: client, config backup, REST-preferred enrichment."""
import json

import pytest

from apps.compliance import collector
from apps.compliance.sonicwall_client import (
    SonicWallAuthError, SonicWallClient, resolve_rest_credentials,
)
from apps.devices import enrich
from apps.devices.models import Device

pytestmark = pytest.mark.django_db

AUTH_OK = {"status": {"info": [{"auth_code": "API_AUTH_SUCCESS", "model": "NSv XS"}]}}
CONFIG = {
    "model": "NSv XS",
    "firmware_version": "SonicOSX 8.2.1-8010",
    "serial_number": "0017-C5F1-0547",
    "system_uptime": "0 Days, 0 Hours, 51 Minutes",
    "administration": {"firewall_name": "soniclab"},
}


class _Resp:
    def __init__(self, payload, status=200):
        self._payload, self.status_code = payload, status

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise Exception(f"HTTP {self.status_code}")


class _FakeSession:
    def __init__(self, auth=AUTH_OK, config=CONFIG):
        self.trust_env = True
        self.verify = None
        self.headers = {}
        self.calls = []
        self._auth, self._config = auth, config

    def post(self, url, **kw):
        self.calls.append(("POST", url, kw))
        return _Resp(self._auth)

    def get(self, url, **kw):
        self.calls.append(("GET", url, kw))
        return _Resp(self._config)

    def delete(self, url, **kw):
        self.calls.append(("DELETE", url, kw))
        return _Resp({})


# ── credential resolution ─────────────────────────────────────────────────────

class TestResolveCredentials:
    def test_prefers_https(self):
        class P:
            https_username = "apiuser"
            https_port = 8443
            ssh_username = "admin"
        u, p, port = resolve_rest_credentials(P(), {"https_password": "hp", "ssh_password": "sp"})
        assert (u, p, port) == ("apiuser", "hp", 8443)

    def test_falls_back_to_ssh(self):
        class P:
            https_username = ""
            https_port = None
            ssh_username = "admin"
        u, p, port = resolve_rest_credentials(P(), {"ssh_password": "sp"})
        assert (u, p, port) == ("admin", "sp", 443)


# ── client ────────────────────────────────────────────────────────────────────

class TestSonicWallClient:
    def test_login_get_config_logout(self):
        c = SonicWallClient("1.2.3.4", "admin", "pw", verify_ssl=False)
        c.session = _FakeSession()
        assert c.login()["auth_code"] == "API_AUTH_SUCCESS"
        assert c.get_config()["serial_number"] == "0017-C5F1-0547"
        c.logout()
        methods = [call[0] for call in c.session.calls]
        assert methods == ["POST", "GET", "DELETE"]
        # The verify=False gotcha guard: every request passes verify explicitly.
        assert all(call[2].get("verify") is False for call in c.session.calls)

    def test_constructor_disables_trust_env(self):
        # Guards against REQUESTS_CA_BUNDLE in the env overriding verify=False.
        c = SonicWallClient("1.2.3.4", "admin", "pw", verify_ssl=False)
        assert c.session.trust_env is False and c.session.verify is False

    def test_login_raises_on_auth_failure(self):
        c = SonicWallClient("1.2.3.4", "admin", "bad")
        c.session = _FakeSession(auth={"status": {"info": [{"auth_code": "API_AUTH_FAILURE", "message": "bad creds"}]}})
        with pytest.raises(SonicWallAuthError):
            c.login()

    def test_context_manager_logs_in_and_out(self):
        c = SonicWallClient("1.2.3.4", "admin", "pw")
        c.session = _FakeSession()
        with c as client:
            client.get_config()
        assert [call[0] for call in c.session.calls] == ["POST", "GET", "DELETE"]


# ── config backup ─────────────────────────────────────────────────────────────

class TestConfigBackup:
    def _fake_client(self, monkeypatch):
        captured = {}

        class FakeClient:
            def __init__(self, host, username, password, port=443, verify_ssl=False, **kw):
                captured.update(host=host, username=username, password=password,
                                port=port, verify_ssl=verify_ssl)

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def get_config(self):
                return CONFIG

        monkeypatch.setattr("apps.compliance.sonicwall_client.SonicWallClient", FakeClient)
        monkeypatch.setattr("apps.compliance.sonicwall_client.resolve_rest_credentials",
                            lambda p, c: ("admin", "pw", 443))
        return captured

    def test_collect_sonicwall_config_returns_pretty_json(self, monkeypatch):
        self._fake_client(monkeypatch)
        dev = Device.objects.create(hostname="fw", ip_address="10.0.0.9",
                                    management_ip="10.0.0.9", platform="sonicwall")
        out = collector.collect_sonicwall_config(dev, dev.credential_profile, {})
        parsed = json.loads(out)
        assert parsed["model"] == "NSv XS"
        assert out == json.dumps(CONFIG, indent=2)   # pretty-printed

    def test_dispatch_routes_sonicwall_to_rest(self, monkeypatch):
        cap = self._fake_client(monkeypatch)
        dev = Device.objects.create(hostname="fw2", ip_address="10.0.0.10",
                                    management_ip="10.0.0.10", platform="sonicwall")
        content = collector._fetch_running_config(dev, {})
        assert json.loads(content)["serial_number"] == "0017-C5F1-0547"
        assert cap["verify_ssl"] is False   # self-signed device cert


# ── REST-preferred enrichment ───────────────────────────────────────────────────

class TestRestEnrichment:
    @pytest.fixture
    def device(self):
        from apps.credentials.models import CredentialProfile
        p = CredentialProfile.objects.create(name="sw", https_enabled=True,
                                             https_username="admin", snmpv2c_enabled=True)
        return Device.objects.create(hostname="device-10.0.0.11", ip_address="10.0.0.11",
                                     management_ip="10.0.0.11", platform="sonicwall",
                                     credential_profile=p)

    def test_parse_sonicwall_rest(self):
        updates: dict = {}
        enrich._parse_sonicwall_rest(
            {"model": "NSv XS", "os_version": "SonicOSX 8.2.1-8010",
             "serial": "0017-C5F1-0547", "hostname": "soniclab"}, updates)
        assert updates == {
            "hostname": "soniclab", "os_version": "SonicOSX 8.2.1-8010",
            "model": "NSv XS", "serial_number": "0017-C5F1-0547",
            "platform": "sonicwall", "vendor": "sonicwall"}

    def test_rest_preferred_snmp_not_called(self, device, monkeypatch):
        monkeypatch.setattr(enrich, "_sonicwall_collect", lambda ip, p, s: {
            "model": "NSv XS", "os_version": "SonicOSX 8.2.1-8010",
            "serial": "0017-C5F1-0547", "hostname": "soniclab"})
        called = {"snmp": False}
        monkeypatch.setattr(enrich, "_snmp_collect", lambda ip, p, s: called.__setitem__("snmp", True) or {})
        monkeypatch.setattr(enrich, "_discover_interfaces", lambda d: ([], 0, 0))
        monkeypatch.setattr(enrich, "_discover_lldp", lambda d, i=None: 0)
        monkeypatch.setattr(enrich, "_publish_topology_updated", lambda did: None)
        enrich.enrich_device(device.id)
        device.refresh_from_db()
        assert device.model == "NSv XS"
        assert device.serial_number == "0017-C5F1-0547"
        assert device.hostname == "soniclab"     # firewall_name adopted
        assert called["snmp"] is False           # REST succeeded → SNMP skipped

    def test_falls_back_to_snmp_when_rest_fails(self, device, monkeypatch):
        monkeypatch.setattr(enrich, "_sonicwall_collect", lambda ip, p, s: {})  # REST down
        snmp = {
            enrich._OID_SYS_DESCR: "SonicWALL NSv XS (SonicOS Enhanced SonicOSX 8.2.1-8010-R9437)",
            enrich._OID_SYS_OBJID: "1.3.6.1.4.1.8741.1",
        }
        monkeypatch.setattr(enrich, "_snmp_collect", lambda ip, p, s: snmp)
        monkeypatch.setattr(enrich, "_discover_interfaces", lambda d: ([], 0, 0))
        monkeypatch.setattr(enrich, "_discover_lldp", lambda d, i=None: 0)
        monkeypatch.setattr(enrich, "_publish_topology_updated", lambda did: None)
        enrich.enrich_device(device.id)
        device.refresh_from_db()
        assert device.model == "NSv XS"
        assert device.os_version == "SonicOSX 8.2.1-8010-R9437"   # from SNMP fallback
