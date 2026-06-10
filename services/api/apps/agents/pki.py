"""
Agent certificate issuance via the OpenBao PKI secrets engine.

Enrollment signs the agent's CSR at ``<mount>/sign/<role>`` and returns the
signed cert + CA chain. Isolated here behind ``issue_agent_certificate`` so the
enrollment view stays simple and tests can monkeypatch it (no live PKI engine).

Setup is automated by ``manage.py setup_agent_pki`` (run from entrypoint.sh on
every api start, idempotent): it creates the ``pki`` mount, the "NetPulse Agent
CA" root, the ``agent`` signing role, and the ``netpulse-agent-pki`` policy.
"""
from __future__ import annotations

from django.conf import settings


class AgentPKIError(Exception):
    """Raised when an agent certificate can't be issued."""


def _mount() -> str:
    return getattr(settings, "AGENT_PKI_MOUNT", "pki")


def _role() -> str:
    return getattr(settings, "AGENT_PKI_ROLE", "agent")


def issue_agent_certificate(hostname: str, csr_pem: str, ttl: str = "8760h") -> dict:
    """Sign an agent CSR via OpenBao PKI. Returns
    ``{certificate, ca_chain, serial, expiration}``. Raises AgentPKIError if
    OpenBao/PKI isn't available or signing fails.
    """
    from apps.credentials import vault

    if not vault.vault_enabled():
        raise AgentPKIError("OpenBao is not configured; cannot issue agent certificates.")
    try:
        client = vault._client()  # reuse the configured hvac client (addr + token)
        resp = client.secrets.pki.sign_certificate(
            name=_role(),
            csr=csr_pem,
            common_name=f"agent.{hostname}",
            mount_point=_mount(),
            # SANs: the agent's own hostname + loopback, so the cert is usable for
            # both mTLS client-auth and local checks. ip_sans needs the role's
            # allow_ip_sans (set by setup_agent_pki).
            extra_params={"ttl": ttl, "format": "pem",
                          "alt_names": hostname, "ip_sans": "127.0.0.1"},
        )
    except AgentPKIError:
        raise
    except Exception as exc:  # noqa: BLE001 — surface a clean enrollment error
        raise AgentPKIError(f"OpenBao PKI signing failed: {exc}") from exc

    data = (resp or {}).get("data") or {}
    cert = data.get("certificate")
    if not cert:
        raise AgentPKIError("OpenBao PKI returned no certificate.")
    ca_chain = data.get("ca_chain") or ([data["issuing_ca"]] if data.get("issuing_ca") else [])
    return {
        "certificate": cert,
        "ca_chain": ca_chain,
        "serial": data.get("serial_number", ""),
        "expiration": data.get("expiration"),
    }


def read_ca_certificate() -> str:
    """Return the agent PKI CA certificate (PEM). Public info — agents fetch it
    to verify the server during mTLS. Raises AgentPKIError if unavailable.
    """
    from apps.credentials import vault

    if not vault.vault_enabled():
        raise AgentPKIError("OpenBao is not configured; no CA certificate available.")
    try:
        pem = vault._client().secrets.pki.read_ca_certificate(mount_point=_mount())
    except Exception as exc:  # noqa: BLE001
        raise AgentPKIError(f"OpenBao PKI CA read failed: {exc}") from exc
    if not (pem or "").strip():
        raise AgentPKIError("Agent PKI CA not initialised; run setup_agent_pki.")
    return pem
