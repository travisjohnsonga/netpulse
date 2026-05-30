"""Tests for NetPulse's own HTTPS server certificate management (apps.tls)."""

import pytest

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def ssl_dir(tmp_path, settings):
    settings.SSL_DIR = str(tmp_path / "ssl")
    return tmp_path / "ssl"


class TestStatus:
    def test_empty_when_no_cert(self, auth_client):
        b = auth_client.get("/api/settings/ssl/").json()
        assert b["installed"] is False and b["expiry_status"] == "none"
        assert b["has_private_key"] is False and b["pending_csr"] is None

    def test_requires_auth(self, api_client):
        assert api_client.get("/api/settings/ssl/").status_code == 401


class TestSelfSigned:
    def test_generate_installs_cert(self, auth_client, ssl_dir):
        resp = auth_client.post("/api/settings/ssl/self-signed/",
                                {"common_name": "netpulse.example.com", "sans": ["10.0.0.1"]}, format="json")
        assert resp.status_code == 201
        b = resp.json()
        assert b["installed"] is True and b["has_private_key"] is True
        assert b["common_name"] == "netpulse.example.com"
        assert b["expiry_status"] == "ok" and b["days_remaining"] > 800
        assert "netpulse.example.com" in b["sans"] and "10.0.0.1" in b["sans"]
        # key + cert on disk, no private key in the response
        assert (ssl_dir / "netpulse.key").exists() and (ssl_dir / "netpulse.crt").exists()
        assert "private_key" not in b and "PRIVATE KEY" not in resp.content.decode()

    def test_key_file_mode_0600(self, auth_client, ssl_dir):
        auth_client.post("/api/settings/ssl/self-signed/", {"common_name": "x"}, format="json")
        assert (ssl_dir / "netpulse.key").stat().st_mode & 0o777 == 0o600


class TestCSRFlow:
    def test_csr_generates_key_and_returns_csr(self, auth_client, ssl_dir):
        resp = auth_client.post("/api/settings/ssl/csr/",
                                {"common_name": "netpulse.example.com", "organization": "Acme"}, format="json")
        assert resp.status_code == 201
        assert "BEGIN CERTIFICATE REQUEST" in resp.json()["csr"]
        assert (ssl_dir / "netpulse.key").exists() and (ssl_dir / "netpulse.csr").exists()
        # status reflects the pending CSR but no installed cert yet
        st = auth_client.get("/api/settings/ssl/").json()
        assert st["installed"] is False and st["pending_csr"] is not None

    def test_get_pending_csr(self, auth_client):
        auth_client.post("/api/settings/ssl/csr/", {"common_name": "x"}, format="json")
        resp = auth_client.get("/api/settings/ssl/csr/")
        assert resp.status_code == 200 and "CERTIFICATE REQUEST" in resp.json()["csr"]

    def test_get_csr_404_when_none(self, auth_client):
        assert auth_client.get("/api/settings/ssl/csr/").status_code == 404


class TestUpload:
    def _make_pair(self):
        """Return (cert_pem, key_pem) for a fresh self-signed cert (test helper)."""
        import datetime as dt
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "uploaded.example.com")])
        now = dt.datetime.now(dt.timezone.utc)
        cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
                .public_key(key.public_key()).serial_number(x509.random_serial_number())
                .not_valid_before(now - dt.timedelta(minutes=1))
                .not_valid_after(now + dt.timedelta(days=365)).sign(key, hashes.SHA256()))
        cert_pem = cert.public_bytes(serialization.Encoding.PEM).decode()
        key_pem = key.private_bytes(serialization.Encoding.PEM,
                                    serialization.PrivateFormat.TraditionalOpenSSL,
                                    serialization.NoEncryption()).decode()
        return cert_pem, key_pem

    def test_upload_cert_with_key(self, auth_client):
        cert_pem, key_pem = self._make_pair()
        resp = auth_client.post("/api/settings/ssl/upload/",
                                {"certificate": cert_pem, "private_key": key_pem}, format="json")
        assert resp.status_code == 200
        b = resp.json()
        assert b["installed"] is True and b["common_name"] == "uploaded.example.com"
        assert "PRIVATE KEY" not in resp.content.decode()  # key never echoed

    def test_upload_mismatched_key_rejected(self, auth_client):
        cert_pem, _ = self._make_pair()
        _, other_key = self._make_pair()
        resp = auth_client.post("/api/settings/ssl/upload/",
                                {"certificate": cert_pem, "private_key": other_key}, format="json")
        assert resp.status_code == 400 and "match" in resp.json()["detail"].lower()

    def test_upload_without_key_requires_existing(self, auth_client):
        cert_pem, _ = self._make_pair()
        resp = auth_client.post("/api/settings/ssl/upload/", {"certificate": cert_pem}, format="json")
        assert resp.status_code == 400  # no key on disk

    def test_upload_invalid_cert(self, auth_client):
        resp = auth_client.post("/api/settings/ssl/upload/",
                                {"certificate": "not a pem", "private_key": "nope"}, format="json")
        assert resp.status_code == 400


def _make_ca_pem(cn="NetPulse Test Root CA", days=400, ca=True):
    """Return a CA (or leaf) cert PEM for trust-store tests."""
    import datetime as dt
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
            .not_valid_after(now + dt.timedelta(days=days))
            .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True)
            .sign(key, hashes.SHA256()))
    return cert.public_bytes(serialization.Encoding.PEM).decode()


class TestCATrustStore:
    def test_list_empty(self, auth_client):
        resp = auth_client.get("/api/settings/ssl/ca-certs/")
        assert resp.status_code == 200 and resp.json() == []

    def test_requires_auth(self, api_client):
        assert api_client.get("/api/settings/ssl/ca-certs/").status_code == 401

    def test_add_ca_builds_bundle(self, auth_client, ssl_dir):
        pem = _make_ca_pem()
        resp = auth_client.post("/api/settings/ssl/ca-certs/",
                                {"name": "Corp Root", "certificate": pem}, format="json")
        assert resp.status_code == 201, resp.content
        body = resp.json()
        assert len(body) == 1
        ca = body[0]
        assert ca["name"] == "Corp Root" and ca["is_root"] is True
        assert ca["expiry_status"] == "ok" and ca["days_remaining"] > 300
        # bundle written to disk and contains the cert
        bundle = ssl_dir / "ca-bundle.crt"
        assert bundle.exists() and "BEGIN CERTIFICATE" in bundle.read_text()

    def test_duplicate_fingerprint_skipped(self, auth_client):
        pem = _make_ca_pem(cn="Dup CA")
        auth_client.post("/api/settings/ssl/ca-certs/", {"certificate": pem}, format="json")
        resp = auth_client.post("/api/settings/ssl/ca-certs/", {"certificate": pem}, format="json")
        assert resp.status_code == 200  # nothing added
        assert len(resp.json()) == 1

    def test_delete_rebuilds(self, auth_client):
        pem = _make_ca_pem(cn="Deletable CA")
        created = auth_client.post("/api/settings/ssl/ca-certs/", {"certificate": pem}, format="json").json()
        cid = created[0]["id"]
        resp = auth_client.delete(f"/api/settings/ssl/ca-certs/{cid}/")
        assert resp.status_code == 204
        assert auth_client.get("/api/settings/ssl/ca-certs/").json() == []

    def test_verify_valid(self, auth_client):
        pem = _make_ca_pem(cn="Verify CA")
        cid = auth_client.post("/api/settings/ssl/ca-certs/", {"certificate": pem}, format="json").json()[0]["id"]
        resp = auth_client.post(f"/api/settings/ssl/ca-certs/{cid}/verify/")
        assert resp.status_code == 200
        assert resp.json()["valid"] is True and resp.json()["expiry_status"] == "ok"

    def test_invalid_upload_rejected(self, auth_client):
        resp = auth_client.post("/api/settings/ssl/ca-certs/", {"certificate": "garbage"}, format="json")
        assert resp.status_code == 400
