"""Issue per-collector mTLS client certificates from the OpenBao PKI engine.

Tolerant of an unconfigured PKI engine: when OpenBao is disabled or the PKI
mount/role isn't set up yet (it's finalised with the NATS leaf transport), this
returns None and enrollment proceeds with the API key alone — the collector is
marked cert-pending rather than failing. The private key is returned to the
agent exactly once and also stored in OpenBao; it never lands in PostgreSQL.
"""
from __future__ import annotations

import hashlib
import logging

from django.conf import settings

logger = logging.getLogger(__name__)


def _pki_config() -> tuple[str, str, str]:
    mount = getattr(settings, "COLLECTOR_PKI_MOUNT", "") or "pki"
    role = getattr(settings, "COLLECTOR_PKI_ROLE", "") or "collector"
    ttl = getattr(settings, "COLLECTOR_CERT_TTL", "") or "720h"
    return mount, role, ttl


def fingerprint_sha256(cert_pem: str) -> str:
    """Colon-separated uppercase SHA-256 fingerprint of a PEM certificate."""
    from cryptography import x509
    from cryptography.hazmat.primitives.serialization import Encoding

    der = x509.load_pem_x509_certificate(cert_pem.encode()).public_bytes(Encoding.DER)
    digest = hashlib.sha256(der).hexdigest().upper()
    return ":".join(digest[i:i + 2] for i in range(0, len(digest), 2))


def issue_collector_cert(collector) -> dict | None:
    """Issue an mTLS cert for `collector`; stamp serial/fingerprint/expiry.

    Returns {"certificate", "private_key", "issuing_ca"} once, or None when PKI
    isn't available. Stores cert+key in OpenBao at netpulse/collectors/{id}/tls.
    """
    from apps.credentials import vault

    if not vault.vault_enabled():
        logger.info("collector PKI: OpenBao disabled — skipping cert issuance for %s", collector.id)
        return None

    mount, role, ttl = _pki_config()
    common_name = f"collector-{collector.id}.netpulse"
    try:
        client = vault._client()
        resp = client.secrets.pki.generate_certificate(
            name=role, common_name=common_name, mount_point=mount,
            extra_params={"ttl": ttl},
        )
        data = resp["data"]
    except Exception as exc:  # noqa: BLE001 — PKI not set up yet / mount missing
        logger.warning("collector PKI: cert issuance unavailable for %s (%s)", collector.id, exc)
        return None

    cert_pem = data["certificate"]
    out = {
        "certificate": cert_pem,
        "private_key": data.get("private_key", ""),
        "issuing_ca": data.get("issuing_ca", ""),
    }
    # Persist the key material in OpenBao (single source of truth), not the DB.
    try:
        vault.write_secret(f"netpulse/collectors/{collector.id}/tls", out)
    except Exception as exc:  # noqa: BLE001
        logger.warning("collector PKI: could not store cert material for %s (%s)", collector.id, exc)

    collector.cert_serial = data.get("serial_number", "")
    collector.cert_fingerprint_sha256 = fingerprint_sha256(cert_pem)
    expiry = data.get("expiration")
    if expiry:
        from datetime import datetime, timezone as _tz
        collector.cert_expires_at = datetime.fromtimestamp(int(expiry), tz=_tz.utc)
    collector.save(update_fields=["cert_serial", "cert_fingerprint_sha256", "cert_expires_at", "updated_at"])
    return out
