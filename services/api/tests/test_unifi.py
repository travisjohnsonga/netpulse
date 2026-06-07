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


class TestSyncController:
    def test_sync_imports_and_stamps(self, roles, monkeypatch):
        c = _controller()
        devices = [{"type": "uap", "name": "A", "ip": "10.0.2.1"},
                   {"type": "usw", "name": "B", "ip": "10.0.2.2"},
                   {"type": "uap", "name": "C"}]  # no ip → skipped
        monkeypatch.setattr("apps.integrations.unifi_client.UnifiClient", lambda *a, **k: FakeClient(devices))
        monkeypatch.setattr(unifi_sync, "_read_password", lambda c: "pw")
        res = unifi_sync.sync_controller(c)
        assert res == {"imported": 2, "updated": 0, "skipped": 1}
        c.refresh_from_db()
        assert c.last_sync is not None and c.device_count == 2 and c.last_error == ""


class TestEndpoints:
    def test_create_writes_password(self, auth_client, monkeypatch):
        writes = {}
        monkeypatch.setattr("apps.credentials.vault.write_secret", lambda p, d: writes.update({p: d}))
        resp = auth_client.post("/api/integrations/unifi/", {
            "name": "HQ", "host": "10.0.0.1", "port": 8443, "username": "admin",
            "unifi_site_id": "default", "password": "secret123",
        }, format="json")
        assert resp.status_code == 201
        cid = resp.json()["id"]
        assert writes == {f"netpulse/integrations/unifi/{cid}": {"password": "secret123"}}

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
        monkeypatch.setattr("apps.integrations.unifi_sync._read_password", lambda c: "pw")
        resp = auth_client.post(f"/api/integrations/unifi/{c.id}/test/", {}, format="json")
        assert resp.status_code == 200
        b = resp.json()
        assert b["connected"] is True and b["device_count"] == 1 and "Guest" in b["sites"]

    def test_sync_endpoint(self, auth_client, roles, monkeypatch):
        c = _controller()
        monkeypatch.setattr("apps.integrations.unifi_client.UnifiClient",
                            lambda *a, **k: FakeClient([{"type": "uap", "name": "X", "ip": "10.5.0.1"}]))
        monkeypatch.setattr("apps.integrations.unifi_sync._read_password", lambda c: "pw")
        resp = auth_client.post(f"/api/integrations/unifi/{c.id}/sync/", {}, format="json")
        assert resp.status_code == 200 and resp.json()["imported"] == 1

    def test_sync_all_endpoint(self, auth_client, roles, monkeypatch):
        _controller(name="A", host="10.0.0.1")
        _controller(name="B", host="10.0.0.2", enabled=False)  # disabled → skipped
        monkeypatch.setattr("apps.integrations.unifi_client.UnifiClient",
                            lambda *a, **k: FakeClient([{"type": "uap", "name": "Z", "ip": "10.6.0.1"}]))
        monkeypatch.setattr("apps.integrations.unifi_sync._read_password", lambda c: "pw")
        resp = auth_client.post("/api/integrations/unifi/sync-all/", {}, format="json")
        assert resp.status_code == 200
        assert resp.json()["controllers"] == 1  # only the enabled one
