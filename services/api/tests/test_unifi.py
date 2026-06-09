"""Tests for the UniFi controller integration (model, sync, endpoints)."""
import pytest

from apps.devices.models import Device, DeviceRole, Site
from apps.integrations import unifi_sync
from apps.integrations.models import UnifiController

pytestmark = pytest.mark.django_db


class FakeClient:
    """Stand-in for UnifiClient used as a context manager in tests."""
    def __init__(self, devices=None, sites=None):
        self._devices = devices or []
        self._sites = sites or [{"name": "default"}]
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False
    def get_devices(self):
        return self._devices
    def get_sites(self):
        return self._sites


def _controller(**kw):
    defaults = dict(name="HQ", host="10.0.0.1", port=8443, username="admin", unifi_site_id="default")
    defaults.update(kw)
    return UnifiController.objects.create(**defaults)


@pytest.fixture
def roles(db):
    for slug, name in (("wireless-ap", "Wireless AP"), ("access-switch", "Access Switch"), ("router", "Router")):
        DeviceRole.objects.get_or_create(slug=slug, defaults={"name": name})


class TestImportDevice:
    def test_creates_ap_with_role_and_platform(self, roles):
        c = _controller()
        raw = {"type": "uap", "name": "AP-Lobby", "ip": "10.0.0.50", "model": "U6-Pro", "version": "6.5.0"}
        assert unifi_sync._import_device(raw, c) == "imported"
        d = Device.objects.get(hostname="AP-Lobby")
        assert d.platform == "unifi_ap" and d.role.slug == "wireless-ap"
        assert d.management_ip == "10.0.0.50" and d.model == "U6-Pro" and d.vendor == "Ubiquiti"

    def test_type_mapping(self, roles):
        c = _controller()
        cases = {"usw": ("unifi_sw", "access-switch"), "ugw": ("unifi_gw", "router"), "udm": ("unifi_udm", "router")}
        for i, (utype, (plat, slug)) in enumerate(cases.items()):
            unifi_sync._import_device({"type": utype, "name": f"d{i}", "ip": f"10.0.1.{i}"}, c)
            d = Device.objects.get(hostname=f"d{i}")
            assert d.platform == plat and d.role.slug == slug

    def test_update_is_idempotent_by_ip(self, roles):
        c = _controller()
        unifi_sync._import_device({"type": "uap", "name": "AP1", "ip": "10.0.0.60", "version": "1.0"}, c)
        assert unifi_sync._import_device({"type": "uap", "name": "AP1", "ip": "10.0.0.60", "version": "2.0"}, c) == "updated"
        assert Device.objects.filter(management_ip="10.0.0.60").count() == 1
        assert Device.objects.get(management_ip="10.0.0.60").os_version == "2.0"

    def test_skips_device_without_ip(self):
        c = _controller()
        assert unifi_sync._import_device({"type": "uap", "name": "ghost"}, c) == "skipped"
        assert not Device.objects.filter(hostname="ghost").exists()

    def test_assigns_controller_site(self, roles):
        site = Site.objects.create(name="HQ-Site")
        c = _controller(site=site)
        unifi_sync._import_device({"type": "uap", "name": "AP-S", "ip": "10.0.0.70"}, c)
        assert Device.objects.get(hostname="AP-S").site_id == site.id


class TestMacNormalize:
    def test_canonicalises_separators(self):
        assert unifi_sync._normalize_mac("AA:BB:CC:DD:EE:FF") == "aa:bb:cc:dd:ee:ff"
        assert unifi_sync._normalize_mac("aa-bb-cc-dd-ee-ff") == "aa:bb:cc:dd:ee:ff"
        assert unifi_sync._normalize_mac("aabb.ccdd.eeff") == "aa:bb:cc:dd:ee:ff"
        assert unifi_sync._normalize_mac("aabbccddeeff") == "aa:bb:cc:dd:ee:ff"

    def test_rejects_invalid(self):
        assert unifi_sync._normalize_mac("") == ""
        assert unifi_sync._normalize_mac(None) == ""
        assert unifi_sync._normalize_mac("not-a-mac") == ""
        assert unifi_sync._normalize_mac("aa:bb:cc") == ""


class TestDuplicateOnIpChange:
    """A UniFi device that changes IP must update in place, not duplicate."""

    def test_mac_keeps_single_record_when_ip_changes(self, roles):
        c = _controller()
        mac = "aa:bb:cc:dd:ee:ff"
        unifi_sync._import_device({"type": "udm", "name": "wco2-mdf-uic-01", "ip": "10.151.1.167",
                                   "mac": mac, "version": "3.0"}, c)
        d1 = Device.objects.get(hostname="wco2-mdf-uic-01")
        assert d1.mac_address == mac

        # Same device, new IP — must match by MAC and update, not create a dup.
        assert unifi_sync._import_device({"type": "udm", "name": "wco2-mdf-uic-01",
                                          "ip": "10.16.133.5", "mac": mac}, c) == "updated"
        assert Device.objects.count() == 1
        d1.refresh_from_db()
        assert d1.ip_address == "10.16.133.5" and d1.management_ip == "10.16.133.5"
        # No "-2" suffix duplicate.
        assert not Device.objects.filter(hostname="wco2-mdf-uic-01-2").exists()

    def test_no_mac_falls_back_to_hostname_platform(self, roles):
        c = _controller()
        unifi_sync._import_device({"type": "uap", "name": "AP-Edge", "ip": "10.0.9.1"}, c)
        # No MAC, new IP, same name+type → matched by hostname+platform.
        assert unifi_sync._import_device({"type": "uap", "name": "AP-Edge", "ip": "10.0.9.99"}, c) == "updated"
        assert Device.objects.filter(hostname__startswith="AP-Edge").count() == 1
        assert Device.objects.get(hostname="AP-Edge").management_ip == "10.0.9.99"

    def test_find_existing_priority_mac_over_ip(self, roles):
        c = _controller()
        unifi_sync._import_device({"type": "uap", "name": "AP-1", "ip": "10.0.8.1",
                                   "mac": "11:22:33:44:55:66"}, c)
        found = unifi_sync.find_existing_unifi_device(
            "11:22:33:44:55:66", "10.0.8.250", "other-name", "unifi_ap")
        assert found is not None and found.hostname == "AP-1"


class TestControllerHostPropagation:
    def test_updates_linked_device_on_host_change(self, roles):
        from apps.integrations.models import UnifiConsoleStatus
        c = _controller(host="10.16.133.5")
        unifi_sync._import_device({"type": "udm", "name": "udm-1", "ip": "10.151.1.167",
                                   "mac": "de:ad:be:ef:00:01"}, c)
        device = Device.objects.get(hostname="udm-1")
        UnifiConsoleStatus.objects.create(device=device, controller=c)

        assert unifi_sync.update_linked_device_host(c) is True
        device.refresh_from_db()
        assert device.management_ip == "10.16.133.5" and device.ip_address == "10.16.133.5"

    def test_noop_without_linked_device(self):
        c = _controller(host="10.16.133.5")
        assert unifi_sync.update_linked_device_host(c) is False

    def test_noop_when_host_is_dns_name(self, roles):
        from apps.integrations.models import UnifiConsoleStatus
        c = _controller(host="unifi.example.com")
        unifi_sync._import_device({"type": "udm", "name": "udm-2", "ip": "10.0.0.5",
                                   "mac": "de:ad:be:ef:00:02"}, c)
        UnifiConsoleStatus.objects.create(device=Device.objects.get(hostname="udm-2"), controller=c)
        assert unifi_sync.update_linked_device_host(c) is False


def _profile(**kw):
    from apps.credentials.models import CredentialProfile
    defaults = dict(name="unifi-creds", https_enabled=True, https_username="admin",
                    vault_path="netpulse/credentials/test")
    defaults.update(kw)
    return CredentialProfile.objects.create(**defaults)


class TestCredentials:
    """Controller credentials now come from a linked CredentialProfile."""

    def test_uses_https_profile_creds(self, monkeypatch):
        c = _controller(credential_profile=_profile())
        monkeypatch.setattr("apps.credentials.vault.read_secret",
                            lambda path: {"https_password": "vaultpw"})
        assert unifi_sync.get_controller_credentials(c) == ("admin", "vaultpw")

    def test_falls_back_to_ssh(self, monkeypatch):
        p = _profile(name="ssh-creds", https_enabled=False, ssh_enabled=True, ssh_username="root")
        c = _controller(credential_profile=p)
        monkeypatch.setattr("apps.credentials.vault.read_secret",
                            lambda path: {"ssh_password": "sshpw"})
        assert unifi_sync.get_controller_credentials(c) == ("root", "sshpw")

    def test_profile_override(self, monkeypatch):
        c = _controller()  # no saved profile
        p = _profile(name="adhoc")
        monkeypatch.setattr("apps.credentials.vault.read_secret",
                            lambda path: {"https_password": "pw"})
        assert unifi_sync.get_controller_credentials(c, profile=p) == ("admin", "pw")

    def test_raises_when_no_profile(self):
        from apps.integrations.unifi_client import UnifiError
        with pytest.raises(UnifiError, match="No credential profile"):
            unifi_sync.get_controller_credentials(_controller())

    def test_raises_when_no_https_or_ssh(self):
        from apps.integrations.unifi_client import UnifiError
        c = _controller(credential_profile=_profile(name="empty", https_enabled=False))
        with pytest.raises(UnifiError, match="no HTTPS or SSH"):
            unifi_sync.get_controller_credentials(c)

    def test_raises_when_password_missing(self, monkeypatch):
        from apps.integrations.unifi_client import UnifiError
        c = _controller(credential_profile=_profile())
        monkeypatch.setattr("apps.credentials.vault.read_secret", lambda path: {})
        with pytest.raises(UnifiError, match="No credentials found"):
            unifi_sync.get_controller_credentials(c)


class TestLogin:
    """Login tries UniFi OS (/api/auth/login) first, then classic (/api/login)."""

    class FakeResp:
        def __init__(self, status_code):
            self.status_code = status_code

    def _client(self, monkeypatch, responses):
        """Build a UnifiClient whose session.post returns queued responses by path."""
        from apps.integrations.unifi_client import UnifiClient
        client = UnifiClient("10.0.0.1", 443, "admin", "pw")
        calls = []

        def fake_post(url, **kw):
            calls.append(url)
            for path, resp in responses.items():
                if url.endswith(path):
                    return resp
            raise AssertionError(f"unexpected POST {url}")

        monkeypatch.setattr(client.session, "post", fake_post)
        return client, calls

    def test_unifi_os_login_sets_proxy_prefix(self, monkeypatch):
        client, calls = self._client(monkeypatch, {"/api/auth/login": self.FakeResp(200)})
        client.login()
        assert client._logged_in and client._unifi_os
        assert client._api("/api/s/default/stat/device") == "/proxy/network/api/s/default/stat/device"
        assert calls == ["https://10.0.0.1:443/api/auth/login"]

    def test_falls_back_to_classic_login(self, monkeypatch):
        client, calls = self._client(monkeypatch, {
            "/api/auth/login": self.FakeResp(401),
            "/api/login": self.FakeResp(200),
        })
        client.login()
        assert client._logged_in and not client._unifi_os
        assert client._api("/api/s/default/stat/device") == "/api/s/default/stat/device"
        assert calls == ["https://10.0.0.1:443/api/auth/login", "https://10.0.0.1:443/api/login"]

    def test_raises_when_both_endpoints_fail(self, monkeypatch):
        from apps.integrations.unifi_client import UnifiError
        client, _ = self._client(monkeypatch, {
            "/api/auth/login": self.FakeResp(401),
            "/api/login": self.FakeResp(401),
        })
        with pytest.raises(UnifiError, match="tried both auth endpoints"):
            client.login()


class TestSyncController:
    def test_sync_imports_and_stamps(self, roles, monkeypatch):
        c = _controller()
        devices = [{"type": "uap", "name": "A", "ip": "10.0.2.1"},
                   {"type": "usw", "name": "B", "ip": "10.0.2.2"},
                   {"type": "uap", "name": "C"}]  # no ip → skipped
        monkeypatch.setattr("apps.integrations.unifi_client.UnifiClient", lambda *a, **k: FakeClient(devices))
        monkeypatch.setattr(unifi_sync, "get_controller_credentials", lambda c, profile=None: ("admin", "pw"))
        res = unifi_sync.sync_controller(c)
        assert res == {"imported": 2, "updated": 0, "skipped": 1}
        c.refresh_from_db()
        assert c.last_sync is not None and c.device_count == 2 and c.last_error == ""


class TestEndpoints:
    def test_create_with_credential_profile(self, auth_client):
        p = _profile(name="hq-creds")
        resp = auth_client.post("/api/integrations/unifi/", {
            "name": "HQ", "host": "10.0.0.1", "port": 8443,
            "unifi_site_id": "default", "credential_profile": p.id,
        }, format="json")
        assert resp.status_code == 201
        body = resp.json()
        assert body["credential_profile"] == p.id
        assert body["credential_profile_name"] == "hq-creds"
        assert "password" not in body

    def test_list(self, auth_client):
        _controller()
        resp = auth_client.get("/api/integrations/unifi/")
        assert resp.status_code == 200
        data = resp.json()
        items = data["results"] if isinstance(data, dict) else data
        assert len(items) == 1 and items[0]["name"] == "HQ"
        assert "password" not in items[0]

    def test_test_endpoint(self, auth_client, monkeypatch):
        c = _controller()
        monkeypatch.setattr("apps.integrations.unifi_client.UnifiClient",
                            lambda *a, **k: FakeClient([{"type": "uap", "ip": "1.1.1.1"}], [{"name": "default"}, {"name": "Guest"}]))
        monkeypatch.setattr("apps.integrations.unifi_sync.get_controller_credentials", lambda c, profile=None: ("admin", "pw"))
        resp = auth_client.post(f"/api/integrations/unifi/{c.id}/test/", {}, format="json")
        assert resp.status_code == 200
        b = resp.json()
        assert b["connected"] is True and b["device_count"] == 1 and "Guest" in b["sites"]

    def test_sync_endpoint(self, auth_client, roles, monkeypatch):
        c = _controller()
        monkeypatch.setattr("apps.integrations.unifi_client.UnifiClient",
                            lambda *a, **k: FakeClient([{"type": "uap", "name": "X", "ip": "10.5.0.1"}]))
        monkeypatch.setattr("apps.integrations.unifi_sync.get_controller_credentials", lambda c, profile=None: ("admin", "pw"))
        resp = auth_client.post(f"/api/integrations/unifi/{c.id}/sync/", {}, format="json")
        assert resp.status_code == 200 and resp.json()["imported"] == 1

    def test_sync_all_endpoint(self, auth_client, roles, monkeypatch):
        _controller(name="A", host="10.0.0.1")
        _controller(name="B", host="10.0.0.2", enabled=False)  # disabled → skipped
        monkeypatch.setattr("apps.integrations.unifi_client.UnifiClient",
                            lambda *a, **k: FakeClient([{"type": "uap", "name": "Z", "ip": "10.6.0.1"}]))
        monkeypatch.setattr("apps.integrations.unifi_sync.get_controller_credentials", lambda c, profile=None: ("admin", "pw"))
        resp = auth_client.post("/api/integrations/unifi/sync-all/", {}, format="json")
        assert resp.status_code == 200
        assert resp.json()["controllers"] == 1  # only the enabled one
