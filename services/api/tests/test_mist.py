"""Tests for the Juniper Mist integration (client, sync, endpoints)."""
import pytest

from apps.devices.models import Device, DeviceRole
from apps.integrations import mist_sync
from apps.integrations.mist_client import MistClient, MistError
from apps.integrations.models import MistIntegration, MistSite

pytestmark = pytest.mark.django_db


@pytest.fixture
def roles(db):
    for slug, name in (("wireless-ap", "Wireless AP"), ("access-switch", "Access Switch"), ("router", "Router")):
        DeviceRole.objects.get_or_create(slug=slug, defaults={"name": name})


class _MistStub:
    """Stand-in for MistClient returning canned org/site/device payloads."""
    def __init__(self, *, org=("org1", "Forgent Power"), sites=None, devices=None, stats=None):
        self._org = org
        self._sites = sites or []
        self._devices = devices or {}
        self._stats = stats or {}
    def resolve_org(self):
        return self._org
    def get_sites(self, org_id):
        return self._sites
    def get_devices(self, site_id):
        return self._devices.get(site_id, [])
    def get_device_stats(self, site_id):
        return self._stats.get(site_id, [])


# ── client ────────────────────────────────────────────────────────────────────
class TestClient:
    # Mist has no /self/orgs endpoint — orgs come from /self privileges (scope=org).
    SELF = {
        "email": "travis@forgent.com",
        "full_name": "Travis Johnson",
        "privileges": [
            {"scope": "org", "org_id": "o1", "name": "Forgent Power", "role": "admin"},
            {"scope": "site", "site_id": "s1", "name": "HQ"},  # non-org → ignored
        ],
    }

    def test_test_connection_extracts_orgs_from_privileges(self, monkeypatch):
        c = MistClient("tok")
        monkeypatch.setattr(c, "get_self", lambda: self.SELF)
        r = c.test_connection()
        assert r == {
            "connected": True, "email": "travis@forgent.com",
            "full_name": "Travis Johnson", "org_count": 1,
            "orgs": [{"id": "o1", "name": "Forgent Power", "role": "admin"}],
        }

    def test_get_orgs_derives_from_self(self, monkeypatch):
        c = MistClient("tok")
        monkeypatch.setattr(c, "get_self", lambda: self.SELF)
        assert c.get_orgs() == [{"id": "o1", "name": "Forgent Power", "role": "admin"}]

    def test_resolve_org(self, monkeypatch):
        c = MistClient("tok")
        monkeypatch.setattr(c, "get_self", lambda: self.SELF)
        assert c.resolve_org() == ("o1", "Forgent Power")

    def test_resolve_org_raises_without_org(self, monkeypatch):
        c = MistClient("tok")
        monkeypatch.setattr(c, "get_self", lambda: {"email": "x@y.com", "privileges": []})
        with pytest.raises(MistError):
            c.resolve_org()

    def test_base_url_uses_region_host(self):
        assert MistClient("t").base_url == "https://api.mist.com/api/v1"
        assert MistClient("t", api_host="api.eu.mist.com").base_url == "https://api.eu.mist.com/api/v1"
        # Tolerate a pasted scheme / trailing slash; blank falls back to default.
        assert MistClient("t", api_host="https://api.ac2.mist.com/").base_url == "https://api.ac2.mist.com/api/v1"
        assert MistClient("t", api_host="").base_url == "https://api.mist.com/api/v1"

    def test_sets_token_header_and_strips_whitespace(self):
        assert MistClient("secret-tok").session.headers["Authorization"] == "Token secret-tok"
        # A pasted trailing newline must not corrupt the header (→ 401).
        c = MistClient("  secret-tok\n")
        assert c.session.headers["Authorization"] == "Token secret-tok"
        assert c.session.trust_env is False


# ── device import ─────────────────────────────────────────────────────────────
class TestImportDevice:
    def test_creates_ap_with_role_platform_vendor(self, roles):
        raw = {"type": "ap", "name": "AP-Lobby", "ip": "10.5.0.50",
               "mac": "5c:5b:35:00:11:22", "model": "AP43", "version": "0.12",
               "status": "connected"}
        assert mist_sync._import_device(raw, None) == "imported"
        d = Device.objects.get(hostname="AP-Lobby")
        assert d.platform == "mist_ap" and d.role.slug == "wireless-ap"
        assert d.vendor == "Juniper" and d.model == "AP43" and d.os_version == "0.12"
        assert d.management_ip == "10.5.0.50" and d.mac_address == "5c:5b:35:00:11:22"
        assert d.is_reachable is True

    def test_type_mapping(self, roles):
        cases = {"ap": ("mist_ap", "wireless-ap"), "switch": ("mist_sw", "access-switch"),
                 "gateway": ("mist_gw", "router")}
        for i, (mtype, (plat, slug)) in enumerate(cases.items()):
            mist_sync._import_device({"type": mtype, "name": f"d{i}", "ip": f"10.6.0.{i}"}, None)
            d = Device.objects.get(hostname=f"d{i}")
            assert d.platform == plat and d.role.slug == slug

    def test_disconnected_status_marks_unreachable(self, roles):
        mist_sync._import_device(
            {"type": "ap", "name": "AP-Down", "ip": "10.5.0.9", "status": "disconnected"}, None)
        assert Device.objects.get(hostname="AP-Down").is_reachable is False

    def test_no_ip_is_skipped(self, roles):
        assert mist_sync._import_device({"type": "ap", "name": "Ghost", "ip": ""}, None) == "skipped"
        assert not Device.objects.filter(hostname="Ghost").exists()

    def test_update_idempotent_by_mac(self, roles):
        mac = "aa:bb:cc:dd:ee:ff"
        mist_sync._import_device({"type": "ap", "name": "AP1", "ip": "10.5.1.1", "mac": mac, "version": "1.0"}, None)
        # Same MAC, new IP → update in place (no duplicate).
        assert mist_sync._import_device(
            {"type": "ap", "name": "AP1", "ip": "10.5.1.2", "mac": mac, "version": "2.0"}, None) == "updated"
        assert Device.objects.filter(mac_address=mac).count() == 1
        d = Device.objects.get(mac_address=mac)
        assert d.os_version == "2.0" and d.management_ip == "10.5.1.2"

    def test_ip_locked_blocks_management_ip_overwrite(self, roles):
        mac = "11:22:33:44:55:66"
        mist_sync._import_device({"type": "ap", "name": "AP-Lk", "ip": "10.5.2.1", "mac": mac}, None)
        d = Device.objects.get(mac_address=mac)
        d.ip_locked = True
        d.management_ip = "192.168.99.99"
        d.save()
        mist_sync._import_device({"type": "ap", "name": "AP-Lk", "ip": "10.5.2.99", "mac": mac}, None)
        d.refresh_from_db()
        assert d.management_ip == "192.168.99.99"  # untouched


class TestMergeDevices:
    def test_merges_stats_into_inventory_by_mac(self):
        devices = [{"name": "AP1", "mac": "aa:bb:cc:dd:ee:01", "model": "AP43", "type": "ap"}]
        stats = [{"mac": "aa:bb:cc:dd:ee:01", "ip": "10.0.0.5", "version": "0.14", "status": "connected"}]
        merged = mist_sync._merge_devices(devices, stats)
        assert merged[0]["ip"] == "10.0.0.5" and merged[0]["version"] == "0.14"
        assert merged[0]["status"] == "connected" and merged[0]["model"] == "AP43"

    def test_includes_stats_only_devices(self):
        merged = mist_sync._merge_devices([], [{"mac": "aa:bb:cc:dd:ee:02", "ip": "10.0.0.6", "type": "ap"}])
        assert len(merged) == 1 and merged[0]["ip"] == "10.0.0.6"


# ── full sync ─────────────────────────────────────────────────────────────────
class TestSync:
    def test_sync_imports_sites_and_devices(self, roles, monkeypatch):
        stub = _MistStub(
            sites=[{"id": "s1", "name": "HQ", "address": "1 Main St", "country_code": "US"}],
            devices={"s1": [{"name": "AP-1", "mac": "aa:bb:cc:dd:ee:10", "model": "AP43", "type": "ap"}]},
            stats={"s1": [{"mac": "aa:bb:cc:dd:ee:10", "ip": "10.7.0.5", "version": "0.14", "status": "connected"}]},
        )
        monkeypatch.setattr("apps.integrations.mist_client._read_api_token", lambda: "tok")
        monkeypatch.setattr("apps.integrations.mist_client.MistClient", lambda *a, **k: stub)
        counts = mist_sync.sync_mist()
        assert counts == {"sites": 1, "imported": 1, "updated": 0, "skipped": 0}

        integ = MistIntegration.load()
        assert integ.org_id == "org1" and integ.org_name == "Forgent Power"
        assert integ.site_count == 1 and integ.device_count == 1 and integ.last_sync is not None
        site = MistSite.objects.get(mist_id="s1")
        assert site.name == "HQ" and site.device_count == 1 and site.country_code == "US"
        assert Device.objects.get(hostname="AP-1").management_ip == "10.7.0.5"

    def test_sync_assigns_linked_netpulse_site(self, roles, monkeypatch):
        from apps.devices.models import Site
        np_site = Site.objects.create(name="Forgent HQ")
        MistSite.objects.create(mist_id="s1", name="HQ", site=np_site)
        stub = _MistStub(
            sites=[{"id": "s1", "name": "HQ"}],
            devices={"s1": [{"name": "AP-2", "mac": "aa:bb:cc:dd:ee:20", "type": "ap"}]},
            stats={"s1": [{"mac": "aa:bb:cc:dd:ee:20", "ip": "10.7.0.9", "status": "connected"}]},
        )
        monkeypatch.setattr("apps.integrations.mist_client._read_api_token", lambda: "tok")
        monkeypatch.setattr("apps.integrations.mist_client.MistClient", lambda *a, **k: stub)
        mist_sync.sync_mist()
        assert Device.objects.get(hostname="AP-2").site_id == np_site.id

    def test_sync_uses_configured_api_host(self, roles, monkeypatch):
        integ = MistIntegration.load()
        integ.api_host = "api.eu.mist.com"
        integ.save()
        captured = {}

        def _factory(token, api_host="api.mist.com", **k):
            captured["host"] = api_host
            return _MistStub(sites=[])
        monkeypatch.setattr("apps.integrations.mist_client._read_api_token", lambda: "tok")
        monkeypatch.setattr("apps.integrations.mist_client.MistClient", _factory)
        mist_sync.sync_mist()
        assert captured["host"] == "api.eu.mist.com"

    def test_sync_without_token_raises(self, monkeypatch):
        monkeypatch.setattr("apps.integrations.mist_client._read_api_token", lambda: "")
        with pytest.raises(MistError):
            mist_sync.sync_mist()
        assert MistIntegration.load().last_error == "No API token configured"

    def test_sync_uses_saved_org_id(self, roles, monkeypatch):
        integ = MistIntegration.load()
        integ.org_id = "saved-org"
        integ.save()
        seen = {}

        class _Stub(_MistStub):
            def get_sites(self, org_id):
                seen["org"] = org_id
                return []
        monkeypatch.setattr("apps.integrations.mist_client._read_api_token", lambda: "tok")
        monkeypatch.setattr("apps.integrations.mist_client.MistClient", lambda *a, **k: _Stub())
        mist_sync.sync_mist()
        assert seen["org"] == "saved-org"  # didn't call resolve_org()


# ── endpoints ─────────────────────────────────────────────────────────────────
class TestEndpoints:
    def test_get_put_account(self, auth_client, monkeypatch):
        writes = {}
        monkeypatch.setattr("apps.credentials.vault.write_secret", lambda p, d: writes.update({p: d}))
        assert auth_client.get("/api/integrations/mist/").status_code == 200
        resp = auth_client.put("/api/integrations/mist/", {"api_token": "tok-abc", "enabled": True}, format="json")
        assert resp.status_code == 200
        assert writes.get("netpulse/integrations/mist") == {"api_token": "tok-abc"}
        assert "api_token" not in resp.json()  # write-only

    def test_test_endpoint(self, auth_client, monkeypatch):
        # The view imports MistClient from mist_client at call time.
        monkeypatch.setattr(
            "apps.integrations.mist_client.MistClient",
            lambda *a, **k: type("C", (), {
                "test_connection": lambda self: {
                    "connected": True, "email": "travis@forgent.com", "org_count": 1,
                    "orgs": [{"id": "o1", "name": "Forgent Power"}]}})())
        resp = auth_client.post("/api/integrations/mist/test/", {"api_token": "k"}, format="json")
        assert resp.status_code == 200
        body = resp.json()
        assert body["connected"] is True and body["email"] == "travis@forgent.com"
        # Org context persisted for display after restart.
        assert MistIntegration.load().org_name == "Forgent Power"

    def test_put_saves_api_host(self, auth_client, monkeypatch):
        monkeypatch.setattr("apps.credentials.vault.write_secret", lambda p, d: None)
        resp = auth_client.put("/api/integrations/mist/", {"api_host": "api.ap.mist.com"}, format="json")
        assert resp.status_code == 200 and resp.json()["api_host"] == "api.ap.mist.com"
        assert MistIntegration.load().api_host == "api.ap.mist.com"

    def test_test_endpoint_passes_api_host(self, auth_client, monkeypatch):
        captured = {}

        def _factory(token, api_host="api.mist.com", **k):
            captured["host"] = api_host
            return type("C", (), {"test_connection": lambda self: {"connected": True, "orgs": []}})()
        monkeypatch.setattr("apps.integrations.mist_client.MistClient", _factory)
        auth_client.post("/api/integrations/mist/test/",
                         {"api_token": "k", "api_host": "api.ap.mist.com"}, format="json")
        assert captured["host"] == "api.ap.mist.com"

    def test_test_endpoint_no_token(self, auth_client):
        resp = auth_client.post("/api/integrations/mist/test/", {}, format="json")
        assert resp.status_code == 400 and resp.json()["connected"] is False

    def test_sync_endpoint(self, auth_client, roles, monkeypatch):
        monkeypatch.setattr("apps.integrations.mist_client._read_api_token", lambda: "tok")
        stub = _MistStub(sites=[{"id": "s1", "name": "HQ"}],
                         devices={"s1": [{"name": "AP-X", "mac": "aa:bb:cc:dd:ee:30", "type": "ap"}]},
                         stats={"s1": [{"mac": "aa:bb:cc:dd:ee:30", "ip": "10.8.0.1", "status": "connected"}]})
        monkeypatch.setattr("apps.integrations.mist_client.MistClient", lambda *a, **k: stub)
        resp = auth_client.post("/api/integrations/mist/sync/", {}, format="json")
        assert resp.status_code == 200 and resp.json()["imported"] == 1

    def test_sites_endpoint(self, auth_client):
        MistSite.objects.create(mist_id="s1", name="HQ", device_count=3)
        resp = auth_client.get("/api/integrations/mist/sites/")
        assert resp.status_code == 200
        data = resp.json()
        rows = data["results"] if isinstance(data, dict) else data
        assert len(rows) == 1 and rows[0]["name"] == "HQ" and rows[0]["device_count"] == 3
