import pytest

from apps.devices.models import Device, Site
from apps.integrations import netbox as netbox_mod
from apps.integrations.models import NetBoxImport

pytestmark = pytest.mark.django_db


# ── Fake NetBox client (no network) ──────────────────────────────────────────


class FakeClient:
    def __init__(self, *args, **kwargs):
        pass

    def detect_version(self):
        return "4.1.3"

    def get_sites(self):
        return [
            {"name": "DC-1", "description": "Primary", "physical_address": "1 Main St"},
            {"name": "DC-2"},
        ]

    def get_devices(self):
        return [
            {
                "name": "nb-rtr-01",
                "primary_ip": {"address": "10.10.0.1/32"},
                "site": {"name": "DC-1"},
                "role": {"name": "core"},  # v4 key
                "device_type": {"manufacturer": {"name": "Cisco"}, "model": "Catalyst 9300"},
                "platform": {"name": "IOS-XE"},
                "status": {"value": "active"},
                "tags": [{"name": "prod"}],
            },
            {  # v3-style device_role + no primary IP → skipped
                "name": "nb-sw-02",
                "primary_ip": None,
                "site": {"name": "DC-2"},
                "device_role": {"name": "access"},
                "device_type": {"manufacturer": {"name": "Arista"}, "model": "7050"},
                "status": {"value": "planned"},
            },
        ]


@pytest.fixture(autouse=True)
def patch_client(monkeypatch):
    monkeypatch.setattr(netbox_mod, "NetBoxClient", FakeClient)


# ── Tests ─────────────────────────────────────────────────────────────────────


class TestNetBoxImport:
    def test_test_connection(self, auth_client):
        resp = auth_client.post("/api/import/netbox/test-connection/", {
            "netbox_url": "https://netbox.example.com", "api_token": "tok",
        }, format="json")
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        assert resp.json()["version"] == "4.1.3"

    def test_import_creates_sites_and_devices(self, auth_client):
        resp = auth_client.post("/api/import/netbox/", {
            "netbox_url": "https://netbox.example.com",
            "api_token": "tok",
            "import_options": {"sites": True, "devices": True},
        }, format="json")
        assert resp.status_code == 201, resp.content
        body = resp.json()
        assert body["sites_imported"] == 2
        assert body["devices_imported"] == 1   # one device skipped (no primary IP)
        assert body["skipped"] == 1
        assert body["netbox_version"] == "4.1.3"
        assert body["status"] == "completed"

        d = Device.objects.get(hostname="nb-rtr-01")
        assert d.ip_address == "10.10.0.1"
        assert d.vendor == "Cisco"
        assert d.platform == "ios_xe"
        assert d.site == Site.objects.get(name="DC-1")
        assert "Role: core" in d.notes

    def test_reimport_upserts_existing(self, auth_client):
        auth_client.post("/api/import/netbox/", {"netbox_url": "https://n.example.com", "api_token": "t"}, format="json")
        before = Device.objects.get(hostname="nb-rtr-01").pk
        resp = auth_client.post("/api/import/netbox/", {"netbox_url": "https://n.example.com", "api_token": "t"}, format="json")
        body = resp.json()
        assert body["devices_imported"] == 0      # nothing new created
        assert body["devices_updated"] >= 1        # existing updated in place
        assert body["sites_imported"] == 0         # get_or_create finds existing
        # PK preserved → stable device identity across re-imports
        assert Device.objects.get(hostname="nb-rtr-01").pk == before

    def test_history_listed(self, auth_client):
        auth_client.post("/api/import/netbox/", {"netbox_url": "https://n.example.com", "api_token": "t"}, format="json")
        resp = auth_client.get("/api/import/netbox/")
        assert resp.status_code == 200
        assert resp.json()["count"] >= 1

    def test_vault_path_recorded_not_token(self, auth_client):
        auth_client.post("/api/import/netbox/", {"netbox_url": "https://n.example.com", "api_token": "supersecret"}, format="json")
        rec = NetBoxImport.objects.latest("created_at")
        assert rec.vault_path.startswith("netpulse/integrations/netbox/")
        # token is never persisted on the model
        assert "supersecret" not in str(rec.__dict__)

    def test_unauthenticated_rejected(self, api_client):
        assert api_client.get("/api/import/netbox/").status_code == 401
