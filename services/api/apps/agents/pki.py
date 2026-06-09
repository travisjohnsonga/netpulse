"""
Agent certificate issuance via the OpenBao PKI secrets engine.

Enrollment signs the agent's CSR at ``<mount>/sign/<role>`` and returns the
signed cert + CA chain. Isolated here behind ``issue_agent_certificate`` so the
enrollment view stays simple and tests can monkeypatch it (no live PKI engine).

Setup (one-time, infra follow-up — see CLAUDE.md):
    bao secrets enable pki
    bao write pki/root/generate/internal common_name="NetPulse Agent CA" ttl=87600h
    bao write pki/roles/agent allowed_domains=agent.netpulse.local \
        allow_subdomains=true max_ttl=8760h key_type=ec key_bits=384
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
            extra_params={"ttl": ttl, "format": "pem"},
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
