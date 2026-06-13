"""
Juniper Mist cloud API client (https://api.mist.com).

Mist is a cloud-only platform: a single org API token (``Authorization: Token
{token}``) lists the org's sites and their managed devices (APs/switches/
gateways). The token lives in OpenBao at MIST_VAULT_PATH; this client only ever
receives the plaintext token at call time.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

MIST_BASE = "https://api.mist.com/api/v1"


class MistError(Exception):
    """A Juniper Mist API request failed."""


class MistClient:
    def __init__(self, api_token: str, timeout: int = 15):
        import requests

        self.timeout = timeout
        self.session = requests.Session()
        # Don't let REQUESTS_CA_BUNDLE/HTTP(S)_PROXY env vars silently alter the
        # request (same defensive default used by the other cloud clients).
        self.session.trust_env = False
        # Mist token auth: "Authorization: Token {token}". Strip stray whitespace
        # (a trailing newline pasted into the UI silently breaks the header → 401).
        self.session.headers.update({
            "Authorization": f"Token {(api_token or '').strip()}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        })

    def _get(self, path: str, timeout: int | None = None):
        try:
            resp = self.session.get(f"{MIST_BASE}{path}", timeout=timeout or self.timeout)
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:  # noqa: BLE001
            raise MistError(f"Mist {path} failed: {exc}") from exc

    def get_self(self) -> dict:
        """Current user + org membership (``/self``)."""
        return self._get("/self")

    @staticmethod
    def _orgs_from_privileges(data: dict) -> list:
        """Extract org memberships from a ``/self`` payload's privileges list.

        Mist has **no** ``/self/orgs`` endpoint — org membership is reported as
        ``privileges`` entries on ``/self`` with ``scope == "org"``.
        """
        return [
            {"id": str(p.get("org_id", "")), "name": p.get("name", "") or "",
             "role": p.get("role", "") or ""}
            for p in (data.get("privileges") or [])
            if p.get("scope") == "org" and p.get("org_id")
        ]

    def get_orgs(self) -> list:
        """Organizations the token can access (derived from ``/self`` privileges)."""
        return self._orgs_from_privileges(self.get_self())

    def get_sites(self, org_id: str) -> list:
        """All sites in an org (``/orgs/{org_id}/sites``)."""
        return self._get(f"/orgs/{org_id}/sites") or []

    def get_devices(self, site_id: str) -> list:
        """Inventory of a site's devices (``/sites/{site_id}/devices``)."""
        return self._get(f"/sites/{site_id}/devices", timeout=30) or []

    def get_device_stats(self, site_id: str) -> list:
        """Live device stats (ip/version/status) (``/sites/{site_id}/stats/devices``)."""
        return self._get(f"/sites/{site_id}/stats/devices", timeout=30) or []

    def resolve_org(self) -> tuple[str, str]:
        """Return ``(org_id, org_name)`` for the token's first accessible org.

        Org membership comes from the ``/self`` privileges (scope == "org").
        Raises :class:`MistError` when the token has no org.
        """
        orgs = self.get_orgs()
        if orgs:
            return orgs[0]["id"], orgs[0]["name"]
        raise MistError("The Mist API token has no accessible organization.")

    def test_connection(self) -> dict:
        """Verify the token and return identity + org summary (from ``/self``)."""
        data = self.get_self()
        orgs = self._orgs_from_privileges(data)
        return {
            "connected": True,
            "email": data.get("email", "") or "",
            "full_name": data.get("full_name", "") or "",
            "org_count": len(orgs),
            "orgs": orgs,
        }


def _read_api_token() -> str:
    from apps.credentials import vault

    from .models import MIST_VAULT_PATH
    try:
        return (vault.read_secret(MIST_VAULT_PATH) or {}).get("api_token", "") or ""
    except Exception as exc:  # noqa: BLE001
        logger.warning("could not read Mist API token: %s", exc)
        return ""
