import pytest

from apps.devices.models import Device, Site
from apps.integrations import netbox as netbox_mod
from apps.integrations.models import NetBoxImport
# Bound before the autouse patch_client fixture swaps netbox_mod.NetBoxClient
# for FakeClient — these SSL tests need the real client.
from apps.integrations.netbox import NetBoxClient as RealNetBoxClient

pytestmark = pytest.mark.django_db


# ── Fake NetBox client (no network) ──────────────────────────────────────────


class FakeClient:
    last_kwargs: dict = {}

    def __init__(self, *args, **kwargs):
        FakeClient.last_kwargs = kwargs

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

    def test_verify_ssl_defaults_true(self, auth_client):
        resp = auth_client.post("/api/import/netbox/", {
            "netbox_url": "https://n.example.com", "api_token": "t",
        }, format="json")
        assert resp.status_code == 201
        assert resp.json()["verify_ssl"] is True
        assert FakeClient.last_kwargs.get("verify_ssl") is True
        assert NetBoxImport.objects.latest("created_at").verify_ssl is True

    def test_verify_ssl_disabled_threads_through(self, auth_client):
        resp = auth_client.post("/api/import/netbox/", {
            "netbox_url": "https://n.example.com", "api_token": "t", "verify_ssl": False,
        }, format="json")
        assert resp.status_code == 201
        assert resp.json()["verify_ssl"] is False
        # Passed to the client and recorded on the import row.
        assert FakeClient.last_kwargs.get("verify_ssl") is False
        assert NetBoxImport.objects.latest("created_at").verify_ssl is False

    def test_test_connection_passes_verify_ssl(self, auth_client):
        auth_client.post("/api/import/netbox/test-connection/", {
            "netbox_url": "https://n.example.com", "api_token": "t", "verify_ssl": False,
        }, format="json")
        assert FakeClient.last_kwargs.get("verify_ssl") is False


class TestNetBoxClientSSL:
    """The real client builds an unverified TLS context only when disabled."""

    def test_verify_true_uses_default_context(self):
        c = RealNetBoxClient("https://n.example.com", "t", verify_ssl=True)
        assert c.verify_ssl is True
        assert c._ssl_ctx is None  # None → urlopen does full verification

    def test_verify_false_disables_checks(self):
        import ssl
        c = RealNetBoxClient("https://n.example.com", "t", verify_ssl=False)
        assert c.verify_ssl is False
        assert c._ssl_ctx is not None
        assert c._ssl_ctx.verify_mode == ssl.CERT_NONE
        assert c._ssl_ctx.check_hostname is False


class TestNetBoxDetectVersion:
    """detect_version reads /api/status/, with a 401/403 fallback for NetBox
    instances that require auth on every endpoint."""

    def test_returns_version_normally(self, monkeypatch):
        c = RealNetBoxClient("https://n.example.com", "t")
        monkeypatch.setattr(c, "_get", lambda path: {"netbox-version": "4.2.1"})
        assert c.detect_version() == "4.2.1"

    def test_falls_back_to_sites_when_status_forbidden(self, monkeypatch):
        c = RealNetBoxClient("https://n.example.com", "t")
        seen = []

        def fake_get(path):
            seen.append(path)
            if path == "/api/status/":
                raise netbox_mod.NetBoxError("NetBox returned HTTP 403 for /api/status/")
            return {"results": []}

        monkeypatch.setattr(c, "_get", fake_get)
        assert c.detect_version() == "unknown"
        assert "/api/dcim/sites/?limit=1" in seen  # confirmed connectivity via an authed read

    def test_reraises_when_fallback_also_unauthorized(self, monkeypatch):
        c = RealNetBoxClient("https://n.example.com", "t")

        def fake_get(path):
            code = "403" if path == "/api/status/" else "401"
            raise netbox_mod.NetBoxError(f"NetBox returned HTTP {code} for {path}")

        monkeypatch.setattr(c, "_get", fake_get)
        with pytest.raises(netbox_mod.NetBoxError):
            c.detect_version()

    def test_reraises_non_auth_errors_without_fallback(self, monkeypatch):
        c = RealNetBoxClient("https://n.example.com", "t")
        seen = []

        def fake_get(path):
            seen.append(path)
            raise netbox_mod.NetBoxError("Could not reach NetBox: timed out")

        monkeypatch.setattr(c, "_get", fake_get)
        with pytest.raises(netbox_mod.NetBoxError, match="Could not reach"):
            c.detect_version()
        assert seen == ["/api/status/"]  # a non-auth error is not retried against sites/


class TestNetBoxPreview:
    def test_preview_does_not_write(self, auth_client):
        resp = auth_client.post("/api/import/netbox/preview/", {
            "netbox_url": "https://n.example.com", "api_token": "t",
        }, format="json")
        assert resp.status_code == 200
        body = resp.json()
        assert body["summary"]["total"] == 2
        assert body["summary"]["will_create"] == 1
        assert body["summary"]["will_skip"] == 1     # nb-sw-02 has no primary IP
        # Nothing persisted by a preview.
        assert not Device.objects.filter(hostname="nb-rtr-01").exists()
        assert not Site.objects.filter(name="DC-1").exists()

    def test_preview_marks_update_and_credential(self, auth_client):
        from apps.credentials.models import CredentialProfile, SiteCredential
        # Pre-create the device + a site credential so preview shows update + cred.
        site = Site.objects.create(name="DC-1")
        cred = CredentialProfile.objects.create(name="dc1-creds")
        SiteCredential.objects.create(site=site, credential_profile=cred, role=None)
        Device.objects.create(hostname="nb-rtr-01", ip_address="10.10.0.1", site=site, platform="other")
        body = auth_client.post("/api/import/netbox/preview/", {
            "netbox_url": "https://n.example.com", "api_token": "t",
        }, format="json").json()
        rtr = next(d for d in body["devices"] if d["hostname"] == "nb-rtr-01")
        assert rtr["action"] == "update" and "platform" in rtr["changes"]
        assert rtr["credential"] == "dc1-creds"
        assert body["credentials"]["assignments"].get("dc1-creds") == 1
