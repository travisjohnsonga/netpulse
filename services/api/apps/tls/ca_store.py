"""
Trusted CA certificate store.

Admins upload CA certificates (PEM, DER, or PKCS#7); we normalise them to PEM,
persist metadata in the DB (apps.tls.models.CACertificate) and rebuild an
on-disk ``ca-bundle.crt`` = the system trust store + every custom CA. That
bundle is what outbound HTTPS uses (REQUESTS_CA_BUNDLE, wired in settings) and
what nginx points ``ssl_trusted_certificate`` at.

Only public certificate material passes through here — never private keys.
"""
from __future__ import annotations

import datetime as _dt
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.serialization import pkcs7
from cryptography.x509.oid import NameOID
from django.conf import settings

BUNDLE_NAME = "ca-bundle.crt"
CA_SUBDIR = "ca-certs"

# Candidate system trust stores, in order. The first that exists is prepended
# to the bundle so trusting a private CA never breaks public TLS.
_SYSTEM_CA_CANDIDATES = [
    "/etc/ssl/certs/ca-certificates.crt",   # Debian/Ubuntu (slim image)
    "/etc/pki/tls/certs/ca-bundle.crt",     # RHEL/Fedora
]


class CAError(Exception):
    """Raised when uploaded CA material can't be parsed."""


def ssl_dir() -> Path:
    d = Path(getattr(settings, "SSL_DIR", "/app/ssl"))
    d.mkdir(parents=True, exist_ok=True)
    return d


def ca_dir() -> Path:
    d = ssl_dir() / CA_SUBDIR
    d.mkdir(parents=True, exist_ok=True)
    return d


def bundle_path() -> Path:
    return ssl_dir() / BUNDLE_NAME


def _system_ca_path() -> Path | None:
    for p in _SYSTEM_CA_CANDIDATES:
        if Path(p).exists():
            return Path(p)
    return None


# ── parsing / normalisation ───────────────────────────────────────────────────


def load_certificates(raw: bytes) -> list[x509.Certificate]:
    """
    Parse one or more certificates from PEM, DER, or PKCS#7 (PEM or DER) bytes.
    Returns a list of cryptography x509.Certificate objects.
    """
    # PEM may hold multiple concatenated certs.
    try:
        certs = x509.load_pem_x509_certificates(raw)
        if certs:
            return certs
    except Exception:
        pass
    # Single DER cert.
    try:
        return [x509.load_der_x509_certificate(raw)]
    except Exception:
        pass
    # PKCS#7 bundle (PEM then DER).
    for loader in (pkcs7.load_pem_pkcs7_certificates, pkcs7.load_der_pkcs7_certificates):
        try:
            certs = loader(raw)
            if certs:
                return certs
        except Exception:
            continue
    raise CAError("could not parse certificate (expected PEM, DER, or PKCS#7)")


def _cn(name: x509.Name) -> str:
    try:
        return name.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value
    except (IndexError, x509.ExtensionNotFound):
        return name.rfc4514_string()


def parse_metadata(cert: x509.Certificate) -> dict:
    """Extract display/storage metadata from a parsed certificate."""
    is_self_signed = cert.subject == cert.issuer
    try:
        bc = cert.extensions.get_extension_for_class(x509.BasicConstraints).value
        is_ca = bool(bc.ca)
    except x509.ExtensionNotFound:
        is_ca = False
    return {
        "subject": _cn(cert.subject),
        "issuer": _cn(cert.issuer),
        "fingerprint_sha256": cert.fingerprint(hashes.SHA256()).hex(),
        "not_before": cert.not_valid_before_utc,
        "not_after": cert.not_valid_after_utc,
        "cert_pem": cert.public_bytes(serialization.Encoding.PEM).decode(),
        "is_root": is_self_signed and is_ca,
        "is_intermediate": (not is_self_signed) and is_ca,
    }


def expiry_status(not_after: _dt.datetime | None) -> tuple[str, int | None]:
    """Return (status, days_remaining). status ∈ none/expired/warning/ok.

    Per spec: red=expired, orange < 90 days, otherwise ok.
    """
    if not_after is None:
        return "none", None
    days = (not_after - _dt.datetime.now(_dt.timezone.utc)).days
    if days < 0:
        return "expired", days
    if days < 90:
        return "warning", days
    return "ok", days


# ── bundle management ─────────────────────────────────────────────────────────


def rebuild_bundle() -> str:
    """
    Rewrite the individual ca-certs/*.crt files and the combined ca-bundle.crt
    (system roots + all custom CAs) from the DB. Returns the bundle path.
    """
    from .models import CACertificate

    # Clear stale per-cert files, then write current ones.
    for old in ca_dir().glob("*.crt"):
        old.unlink(missing_ok=True)

    pieces: list[str] = []
    system = _system_ca_path()
    if system:
        pieces.append(system.read_text())

    for ca in CACertificate.objects.all():
        pem = ca.cert_pem if ca.cert_pem.endswith("\n") else ca.cert_pem + "\n"
        path = ca_dir() / f"{ca.pk}.crt"
        path.write_text(pem)
        if not ca.file_path:
            CACertificate.objects.filter(pk=ca.pk).update(file_path=str(path))
        pieces.append(pem)

    bundle = bundle_path()
    bundle.write_text("\n".join(pieces))
    # Make the running process trust the rebuilt bundle immediately (new
    # processes pick it up via settings on import).
    import os
    os.environ["REQUESTS_CA_BUNDLE"] = str(bundle)
    os.environ["SSL_CERT_FILE"] = str(bundle)
    return str(bundle)
