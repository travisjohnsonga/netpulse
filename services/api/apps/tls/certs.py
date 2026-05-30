"""
Crypto operations for NetPulse's own HTTPS server certificate.

All key material is written to SSL_DIR on disk (private key mode 0600). The
private key is NEVER returned to callers — only the (public) CSR and parsed
certificate metadata leave this module.
"""

from __future__ import annotations

import datetime as _dt
import ipaddress
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from django.conf import settings


class CertError(Exception):
    """Raised when certificate material is invalid or inconsistent."""


KEY_NAME = "netpulse.key"
CERT_NAME = "netpulse.crt"
CSR_NAME = "netpulse.csr"
CHAIN_NAME = "netpulse-chain.crt"


def ssl_dir() -> Path:
    d = Path(getattr(settings, "SSL_DIR", "/app/ssl"))
    d.mkdir(parents=True, exist_ok=True)
    return d


def _key_path() -> Path:
    return ssl_dir() / KEY_NAME


def _cert_path() -> Path:
    return ssl_dir() / CERT_NAME


def _csr_path() -> Path:
    return ssl_dir() / CSR_NAME


def _write_key(key) -> None:
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    )
    path = _key_path()
    path.write_bytes(pem)
    path.chmod(0o600)


def _new_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _subject_name(common_name: str, organization: str = "", country: str = "") -> x509.Name:
    attrs = [x509.NameAttribute(NameOID.COMMON_NAME, common_name)]
    if organization:
        attrs.append(x509.NameAttribute(NameOID.ORGANIZATION_NAME, organization))
    if country:
        attrs.append(x509.NameAttribute(NameOID.COUNTRY_NAME, country))
    return x509.Name(attrs)


def _san_extension(common_name: str, sans: list[str]) -> x509.SubjectAlternativeName:
    names = list(dict.fromkeys([common_name, *sans]))  # dedupe, preserve order
    general: list[x509.GeneralName] = []
    for n in names:
        if not n:
            continue
        try:
            general.append(x509.IPAddress(ipaddress.ip_address(n)))
        except ValueError:
            general.append(x509.DNSName(n))
    return x509.SubjectAlternativeName(general)


# ── operations ────────────────────────────────────────────────────────────────


def generate_self_signed(common_name: str, sans: list[str] | None = None, days: int = 825) -> bytes:
    """Generate a key + self-signed cert, install both, return the cert PEM."""
    sans = sans or []
    key = _new_key()
    now = _dt.datetime.now(_dt.timezone.utc)
    subject = _subject_name(common_name)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - _dt.timedelta(minutes=1))
        .not_valid_after(now + _dt.timedelta(days=days))
        .add_extension(_san_extension(common_name, sans), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    _write_key(key)
    cert_pem = cert.public_bytes(serialization.Encoding.PEM)
    _cert_path().write_bytes(cert_pem)
    # A self-signed install supersedes any pending CSR.
    _csr_path().unlink(missing_ok=True)
    return cert_pem


def generate_csr(common_name: str, sans: list[str] | None = None,
                 organization: str = "", country: str = "") -> str:
    """
    Generate a key + CSR. The key is written to disk; the CSR PEM is returned
    so an admin can send it to a CA. The cert is installed later via upload().
    """
    sans = sans or []
    key = _new_key()
    csr = (
        x509.CertificateSigningRequestBuilder()
        .subject_name(_subject_name(common_name, organization, country))
        .add_extension(_san_extension(common_name, sans), critical=False)
        .sign(key, hashes.SHA256())
    )
    _write_key(key)
    csr_pem = csr.public_bytes(serialization.Encoding.PEM)
    _csr_path().write_bytes(csr_pem)
    return csr_pem.decode()


def _load_cert(cert_pem: bytes) -> x509.Certificate:
    try:
        return x509.load_pem_x509_certificate(cert_pem)
    except Exception as exc:  # noqa: BLE001
        raise CertError(f"invalid certificate PEM: {exc}") from exc


def install_uploaded(cert_pem: str, private_key_pem: str | None = None,
                     chain_pem: str | None = None) -> bytes:
    """
    Install an uploaded certificate. If a private key is supplied it is written
    to disk; otherwise the existing on-disk key (from a prior CSR) is used. The
    cert's public key must match the private key, or CertError is raised.
    """
    cert = _load_cert(cert_pem.encode())

    if private_key_pem:
        try:
            key = serialization.load_pem_private_key(private_key_pem.encode(), password=None)
        except Exception as exc:  # noqa: BLE001
            raise CertError(f"invalid private key PEM: {exc}") from exc
    else:
        if not _key_path().exists():
            raise CertError("no private key on disk — generate a CSR first or upload the key")
        key = serialization.load_pem_private_key(_key_path().read_bytes(), password=None)

    if cert.public_key().public_numbers() != key.public_key().public_numbers():
        raise CertError("certificate does not match the private key")

    if private_key_pem:
        _write_key(key)
    _cert_path().write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    if chain_pem:
        (ssl_dir() / CHAIN_NAME).write_bytes(chain_pem.encode())
    _csr_path().unlink(missing_ok=True)  # CSR fulfilled
    return cert.public_bytes(serialization.Encoding.PEM)


# ── parsing / status ────────────────────────────────────────────────────────


def parse_cert(cert_pem: bytes) -> dict:
    cert = _load_cert(cert_pem)
    try:
        cn = cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value
    except (IndexError, x509.ExtensionNotFound):
        cn = ""
    try:
        issuer = cert.issuer.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value
    except (IndexError, x509.ExtensionNotFound):
        issuer = cert.issuer.rfc4514_string()
    try:
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName)
        sans = [str(g.value) for g in san.value]
    except x509.ExtensionNotFound:
        sans = []
    return {
        "common_name": cn,
        "issuer": issuer,
        "sans": sans,
        "serial": format(cert.serial_number, "x"),
        "fingerprint_sha256": cert.fingerprint(hashes.SHA256()).hex(),
        "not_before": cert.not_valid_before_utc,
        "not_after": cert.not_valid_after_utc,
    }


def expiry_status(not_after: _dt.datetime | None, not_before: _dt.datetime | None = None) -> tuple[str, int | None]:
    """Return (status, days_remaining). status ∈ none/not_yet_valid/expired/critical/warning/ok."""
    if not_after is None:
        return "none", None
    now = _dt.datetime.now(_dt.timezone.utc)
    if not_before and now < not_before:
        return "not_yet_valid", (not_after - now).days
    days = (not_after - now).days
    if days < 0:
        return "expired", days
    if days < 7:
        return "critical", days
    if days < 30:
        return "warning", days
    return "ok", days


def current_status() -> dict:
    """Read the installed cert (if any) from disk and return display metadata."""
    cert_path = _cert_path()
    has_key = _key_path().exists()
    pending_csr = _csr_path().read_text() if _csr_path().exists() else None

    if not cert_path.exists():
        return {
            "installed": False, "has_private_key": has_key,
            "pending_csr": pending_csr, "expiry_status": "none",
            "days_remaining": None, "common_name": "", "issuer": "",
            "sans": [], "serial": "", "fingerprint_sha256": "",
            "not_before": None, "not_after": None,
        }

    meta = parse_cert(cert_path.read_bytes())
    status, days = expiry_status(meta["not_after"], meta["not_before"])
    return {
        **meta, "installed": True, "has_private_key": has_key,
        "pending_csr": pending_csr, "expiry_status": status, "days_remaining": days,
    }
