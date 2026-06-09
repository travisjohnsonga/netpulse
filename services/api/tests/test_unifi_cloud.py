"""Tests for the UniFi Site Manager (cloud) integration."""
import pytest

from apps.integrations import unifi_cloud
from apps.integrations.models import UnifiCloudAccount, UnifiController

pytestmark = pytest.mark.django_db


def _host(hid, hostname, ip, htype="console", model="UDM-Pro", version="4.1.13", network=True):
    state = {"hostname": hostname, "version": version, "hardware": {"name": model}}
    if ip:
        state["ip"] = ip
    if network:
        state["controllers"] = [{"name": "network", "isRunning": True}]
    return {"id": hid, "type": htype, "hardwareId": model, "reportedState": state}


class TestMapping:
    def test_console_maps_to_443(self):
        f = unifi_cloud._host_to_controller_fields(_host("h1", "udm", "192.168.1.1", "console"))
        assert f == {"name": "udm", "host": "192.168.1.1", "port": 443, "cloud_host_id": "h1",
                     "model": "UDM-Pro", "version": "4.1.13"}

    def test_cloudkey_maps_to_8443(self):
        f = unifi_cloud._host_to_controller_fields(_host("h2", "ck", "10.0.0.1", "cloudKey"))
        assert f["port"] == 8443

    def test_no_ip_returns_none(self):
        assert unifi_cloud._host_to_controller_fields(_host("h3", "ghost", None)) is None

    def test_mgmt_port_override(self):
        host = _host("h1", "ck", "10.0.0.1", "cloudKey")
        host["reportedState"]["mgmt_port"] = 443
        assert unifi_cloud._host_to_controller_fields(host)["port"] == 443

    def test_falls_back_to_ipaddrs(self):
        host = _host("h1", "udm", None)
        host["reportedState"]["ipAddrs"] = ["fe80::1", "10.7.40.43", "10.1.99.50"]
        f = unifi_cloud._host_to_controller_fields(host)
        assert f["host"] == "10.7.40.43"  # first IPv4, IPv6 skipped

    def test_falls_back_to_wan_ipv4(self):
        host = _host("h1", "udm", None)
        host["reportedState"]["wans"] = [{"ipv4": "203.0.113.5"}]
        assert unifi_cloud._host_to_controller_fields(host)["host"] == "203.0.113.5"

    def test_model_from_hardware_shortname(self):
        host = _host("h1", "udm", "10.0.0.1")
        host["reportedState"]["hardware"] = {"shortname": "UDMPROMAX"}
        assert unifi_cloud._host_to_controller_fields(host)["model"] == "UDMPROMAX"


class TestDiscover:
    def test_creates_and_updates(self, monkeypatch):
        monkeypatch.setattr(unifi_cloud, "_read_api_key", lambda: "key")
        hosts = [_host("h1", "HQ UDM", "192.168.1.1", "console"),
                 _host("h2", "Office CK", "10.1.0.1", "cloudKey")]
        monkeypatch.setattr("apps.integrations.unifi_cloud.UnifiCloudClient",
                            lambda *a, **k: type("C", (), {"get_hosts": lambda self: hosts})())
        res = unifi_cloud.discover_controllers()
        assert res["discovered"] == 2
        assert {c["status"] for c in res["controllers"]} == {"created"}
        assert UnifiController.objects.filter(cloud_host_id="h1").exists()

        # Re-discover → updated, no duplicates.
        res2 = unifi_cloud.discover_controllers()
        assert all(c["status"] == "updated" for c in res2["controllers"])
        assert UnifiController.objects.filter(cloud_host_id="h1").count() == 1
        acct = UnifiCloudAccount.load()
        assert acct.host_count == 2 and acct.last_sync is not None

    def test_no_key_raises(self, monkeypatch):
        monkeypatch.setattr(unifi_cloud, "_read_api_key", lambda: "")
        with pytest.raises(unifi_cloud.UnifiCloudError):
            unifi_cloud.discover_controllers()

    def test_skips_hosts_without_network_controller(self, monkeypatch):
        monkeypatch.setattr(unifi_cloud, "_read_api_key", lambda: "key")
        hosts = [_host("h1", "HQ UDM", "192.168.1.1", "console"),
                 _host("h2", "Protect-only", "10.1.0.1", "console", network=False)]
        monkeypatch.setattr("apps.integrations.unifi_cloud.UnifiCloudClient",
                            lambda *a, **k: type("C", (), {"get_hosts": lambda self: hosts})())
        res = unifi_cloud.discover_controllers()
        assert res["discovered"] == 1
        assert not UnifiController.objects.filter(cloud_host_id="h2").exists()

    def test_persists_model_and_version(self, monkeypatch):
        monkeypatch.setattr(unifi_cloud, "_read_api_key", lambda: "key")
        hosts = [_host("h1", "HQ", "192.168.1.1", model="UDM-Pro", version="4.1.13")]
        monkeypatch.setattr("apps.integrations.unifi_cloud.UnifiCloudClient",
                            lambda *a, **k: type("C", (), {"get_hosts": lambda self: hosts})())
        unifi_cloud.discover_controllers()
        ctrl = UnifiController.objects.get(cloud_host_id="h1")
        assert ctrl.model == "UDM-Pro" and ctrl.version == "4.1.13"


class TestPagination:
    def test_follows_next_token(self, monkeypatch):
        # Two pages: first returns nextToken, second has none.
        pages = [
            {"data": [_host("h1", "a", "10.0.0.1")], "nextToken": "TOK2"},
            {"data": [_host("h2", "b", "10.0.0.2")]},
        ]
        calls = {"n": 0}

        class FakeResp:
            def __init__(self, payload): self._p = payload
            def raise_for_status(self): pass
            def json(self): return self._p

        class FakeSession:
            trust_env = True
            headers = {}
            def update(self, *a, **k): pass
            def get(self, url, params=None, timeout=None):
                i = calls["n"]; calls["n"] += 1
                # page 2 must be requested with the nextToken from page 1
                if i == 1:
                    assert params.get("nextToken") == "TOK2"
                return FakeResp(pages[i])

        sess = FakeSession(); sess.headers = {}
        monkeypatch.setattr("requests.Session", lambda: sess)
        client = unifi_cloud.UnifiCloudClient("key")
        hosts = client.get_hosts()
        assert len(hosts) == 2 and calls["n"] == 2


class TestEndpoints:
    def test_get_put_cloud(self, auth_client, monkeypatch):
        writes = {}
        monkeypatch.setattr("apps.credentials.vault.write_secret", lambda p, d: writes.update({p: d}))
        assert auth_client.get("/api/integrations/unifi/cloud/").status_code == 200
        resp = auth_client.put("/api/integrations/unifi/cloud/", {"api_key": "UI-abc123", "enabled": True}, format="json")
        assert resp.status_code == 200
        assert writes.get("netpulse/integrations/unifi/cloud") == {"api_key": "UI-abc123"}
        assert "api_key" not in resp.json()  # write-only

    def test_cloud_test(self, auth_client, monkeypatch):
        monkeypatch.setattr("apps.integrations.unifi_cloud.UnifiCloudClient",
                            lambda *a, **k: type("C", (), {"get_hosts": lambda self: [1, 2, 3]})())
        resp = auth_client.post("/api/integrations/unifi/cloud/test/", {"api_key": "k"}, format="json")
        assert resp.status_code == 200 and resp.json() == {"connected": True, "host_count": 3}

    def test_cloud_discover(self, auth_client, monkeypatch):
        monkeypatch.setattr("apps.integrations.unifi_cloud._read_api_key", lambda: "k")
        monkeypatch.setattr("apps.integrations.unifi_cloud.UnifiCloudClient",
                            lambda *a, **k: type("C", (), {"get_hosts": lambda self: [_host("h9", "X", "10.9.9.9")]})())
        resp = auth_client.post("/api/integrations/unifi/cloud/discover/", {}, format="json")
        assert resp.status_code == 200 and resp.json()["discovered"] == 1
