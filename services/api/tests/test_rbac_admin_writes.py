"""RBAC Track 1: sensitive write surfaces are admin-only.

Credential profiles, integration secrets (UniFi/Mist/SMTP), and the TLS/CA trust
store may now be MUTATED only by admins. Non-admins (engineer role here) keep
read access and the operational probes (test/sync/verify) but get 403 on writes,
and the underlying object / OpenBao secret is left unchanged.

This is the hardcoded-AdminOnly form (Track 2 replaces it with capabilities).
"""
import datetime as dt

import pytest

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def ssl_dir(tmp_path, settings):
    # TLS cert ops write under SSL_DIR; point it at a temp dir for the suite.
    settings.SSL_DIR = str(tmp_path / "ssl")
    return tmp_path / "ssl"


def _ca_pem(cn="RBAC Test Root CA"):
    """A self-signed CA cert PEM for trust-store tests."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
    now = dt.datetime.now(dt.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - dt.timedelta(minutes=1))
            .not_valid_after(now + dt.timedelta(days=400))
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .sign(key, hashes.SHA256()))
    return cert.public_bytes(serialization.Encoding.PEM).decode()


# ── Credential profiles ───────────────────────────────────────────────────────

class TestCredentialProfileRBAC:
    URL = "/api/credentials/"
    PAYLOAD = {"name": "rbac-cred", "ssh_enabled": True, "ssh_username": "admin",
               "ssh_auth_method": "password", "ssh_password": "s3cret"}

    def _make(self, **kw):
        from apps.credentials.models import CredentialProfile
        return CredentialProfile.objects.create(
            name=kw.get("name", "seed-cred"), ssh_enabled=True, ssh_username="x")

    def test_engineer_cannot_create(self, engineer_client):
        from apps.credentials.models import CredentialProfile
        before = CredentialProfile.objects.count()
        assert engineer_client.post(self.URL, self.PAYLOAD, format="json").status_code == 403
        assert CredentialProfile.objects.count() == before

    def test_engineer_cannot_update(self, engineer_client):
        p = self._make()
        assert engineer_client.patch(f"{self.URL}{p.id}/", {"description": "x"},
                                     format="json").status_code == 403
        p.refresh_from_db()
        assert p.description == ""

    def test_engineer_cannot_delete_and_secret_untouched(self, engineer_client, monkeypatch):
        from apps.credentials import vault
        from apps.credentials.models import CredentialProfile
        deleted = []
        monkeypatch.setattr(vault, "delete_secret", lambda *a, **k: deleted.append(1))
        p = self._make()
        assert engineer_client.delete(f"{self.URL}{p.id}/").status_code == 403
        assert CredentialProfile.objects.filter(id=p.id).exists()
        assert deleted == []   # OpenBao secret was NOT deleted

    def test_admin_can_create(self, admin_client):
        assert admin_client.post(self.URL, self.PAYLOAD, format="json").status_code == 201

    def test_engineer_can_read(self, engineer_client):
        p = self._make()
        assert engineer_client.get(self.URL).status_code == 200
        assert engineer_client.get(f"{self.URL}{p.id}/").status_code == 200

    def test_engineer_can_run_operational_probes(self, engineer_client):
        p = self._make()
        # test/ stays open (missing ?ip= → 400, NOT 403); devices/ read stays open.
        assert engineer_client.post(f"{self.URL}{p.id}/test/").status_code != 403
        assert engineer_client.get(f"{self.URL}{p.id}/devices/").status_code == 200


# ── Integrations: UniFi / Mist / Email ────────────────────────────────────────

class TestUnifiRBAC:
    URL = "/api/integrations/unifi/"

    def _profile(self):
        from apps.credentials.models import CredentialProfile
        return CredentialProfile.objects.create(name="unifi-cp", ssh_enabled=True, ssh_username="x")

    def _controller(self):
        from apps.integrations.models import UnifiController
        return UnifiController.objects.create(name="HQ", host="10.0.0.1", port=8443,
                                              unifi_site_id="default")

    def test_engineer_cannot_create(self, engineer_client):
        from apps.integrations.models import UnifiController
        before = UnifiController.objects.count()
        body = {"name": "X", "host": "10.0.0.9", "port": 8443, "unifi_site_id": "default",
                "credential_profile": self._profile().id}
        assert engineer_client.post(self.URL, body, format="json").status_code == 403
        assert UnifiController.objects.count() == before

    def test_engineer_cannot_update_or_delete(self, engineer_client):
        c = self._controller()
        assert engineer_client.patch(f"{self.URL}{c.id}/", {"name": "Z"},
                                     format="json").status_code == 403
        assert engineer_client.delete(f"{self.URL}{c.id}/").status_code == 403
        c.refresh_from_db()
        assert c.name == "HQ"

    def test_admin_can_create(self, admin_client):
        body = {"name": "Y", "host": "10.0.0.8", "port": 8443, "unifi_site_id": "default",
                "credential_profile": self._profile().id}
        assert admin_client.post(self.URL, body, format="json").status_code == 201

    def test_engineer_can_read(self, engineer_client):
        c = self._controller()
        assert engineer_client.get(self.URL).status_code == 200
        assert engineer_client.get(f"{self.URL}{c.id}/").status_code == 200

    def test_cloud_account_secret_write_is_admin_only(self, engineer_client, admin_client):
        # GET (read) stays open to engineers; PUT (writes the cloud API key) is admin-only.
        assert engineer_client.get(f"{self.URL}cloud/").status_code == 200
        assert engineer_client.put(f"{self.URL}cloud/", {"enabled": True},
                                   format="json").status_code == 403
        assert admin_client.put(f"{self.URL}cloud/", {"enabled": True},
                                format="json").status_code == 200


class TestMistRBAC:
    URL = "/api/integrations/mist/"

    def test_engineer_cannot_update(self, engineer_client):
        assert engineer_client.put(self.URL, {"enabled": True}, format="json").status_code == 403

    def test_admin_can_update(self, admin_client):
        assert admin_client.put(self.URL, {"enabled": True}, format="json").status_code == 200

    def test_engineer_can_read_and_probe(self, engineer_client):
        assert engineer_client.get(self.URL).status_code == 200
        # test/ stays open (no token configured → 400, NOT 403).
        assert engineer_client.post(f"{self.URL}test/").status_code != 403


class TestEmailSettingsRBAC:
    URL = "/api/integrations/email/"

    def test_engineer_cannot_put(self, engineer_client):
        assert engineer_client.put(self.URL, {"host": "smtp.example.com"},
                                   format="json").status_code == 403

    def test_admin_can_put(self, admin_client):
        assert admin_client.put(self.URL, {"host": "smtp.example.com"},
                                format="json").status_code == 200

    def test_engineer_can_read_and_test(self, engineer_client):
        assert engineer_client.get(self.URL).status_code == 200
        # email/test/ stays open (missing 'to' → 400, NOT 403).
        assert engineer_client.post(f"{self.URL}test/").status_code != 403


# ── TLS / CA trust store ───────────────────────────────────────────────────────

class TestTlsRBAC:
    def test_self_signed_admin_only(self, engineer_client, admin_client):
        assert engineer_client.post("/api/settings/ssl/self-signed/",
                                    {"common_name": "x"}, format="json").status_code == 403
        assert admin_client.post("/api/settings/ssl/self-signed/",
                                 {"common_name": "x"}, format="json").status_code == 201

    def test_csr_admin_only(self, engineer_client, admin_client):
        # The whole CSR view is admin cert-management (GET + POST).
        assert engineer_client.post("/api/settings/ssl/csr/",
                                    {"common_name": "x"}, format="json").status_code == 403
        assert engineer_client.get("/api/settings/ssl/csr/").status_code == 403
        assert admin_client.post("/api/settings/ssl/csr/",
                                 {"common_name": "x"}, format="json").status_code == 201

    def test_upload_admin_only(self, engineer_client):
        assert engineer_client.post("/api/settings/ssl/upload/",
                                    {"certificate": "x"}, format="json").status_code == 403

    def test_status_read_open_to_engineer(self, engineer_client):
        assert engineer_client.get("/api/settings/ssl/").status_code == 200

    def test_ca_trust_store_admin_only_writes(self, engineer_client, admin_client):
        from apps.tls.models import CACertificate
        # GET list stays open.
        assert engineer_client.get("/api/settings/ssl/ca-certs/").status_code == 200
        # Engineer cannot add — trust store unchanged.
        before = CACertificate.objects.count()
        assert engineer_client.post("/api/settings/ssl/ca-certs/",
                                    {"certificate": _ca_pem()}, format="json").status_code == 403
        assert CACertificate.objects.count() == before
        # Admin can add.
        r = admin_client.post("/api/settings/ssl/ca-certs/",
                              {"certificate": _ca_pem("Admin Added CA")}, format="json")
        assert r.status_code in (200, 201)
        ca = CACertificate.objects.get(name="Admin Added CA")
        # verify/ is an operational probe — open to engineers.
        assert engineer_client.post(f"/api/settings/ssl/ca-certs/{ca.id}/verify/").status_code == 200
        # Engineer cannot delete — CA still present.
        assert engineer_client.delete(f"/api/settings/ssl/ca-certs/{ca.id}/").status_code == 403
        assert CACertificate.objects.filter(id=ca.id).exists()
        # Admin can delete.
        assert admin_client.delete(f"/api/settings/ssl/ca-certs/{ca.id}/").status_code == 204
