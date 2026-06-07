"""
UniFi Site Manager (cloud) API — auto-discover all controllers on a UI.com
account from a single API key (https://api.ui.com, X-API-Key header).

The cloud API lists controllers/hosts but NOT their managed devices; device sync
still needs per-controller local credentials. ``discover_controllers`` upserts a
UnifiController row per cloud host (keyed by cloud_host_id).
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

SITE_MANAGER_BASE = "https://api.ui.com"


class UnifiCloudError(Exception):
    """A UniFi Site Manager API request failed."""


class UnifiCloudClient:
    def __init__(self, api_key: str, timeout: int = 15):
        import requests

        self.api_key = api_key
        self.timeout = timeout
        self.session = requests.Session()
        self.session.trust_env = False
        self.session.headers.update({"X-API-Key": api_key, "Accept": "application/json"})

    def _paginate(self, path: str) -> list:
        items: list = []
        params = {"pageSize": 25}
        while True:
            try:
                resp = self.session.get(f"{SITE_MANAGER_BASE}{path}", params=params, timeout=self.timeout)
                resp.raise_for_status()
                data = resp.json()
            except Exception as exc:  # noqa: BLE001
                raise UnifiCloudError(f"UniFi Site Manager {path} failed: {exc}") from exc
            items.extend(data.get("data", []) or [])
            next_token = data.get("nextToken")
            if not next_token:
                break
            params["nextToken"] = next_token
        return items

    def get_hosts(self) -> list:
        """All controllers/hosts on the account."""
        return self._paginate("/v1/hosts")

    def get_sites(self) -> list:
        """All sites across all controllers on the account."""
        return self._paginate("/v1/sites")


def _read_api_key() -> str:
    from apps.credentials import vault
    from .models import UNIFI_CLOUD_VAULT_PATH
    try:
        return (vault.read_secret(UNIFI_CLOUD_VAULT_PATH) or {}).get("api_key", "") or ""
    except Exception as exc:  # noqa: BLE001
        logger.warning("could not read UniFi cloud API key: %s", exc)
        return ""


def _host_to_controller_fields(host: dict) -> dict | None:
    """
    Map a Site Manager host record to UnifiController fields. Returns None when
    the host has no usable management IP.
    """
    state = host.get("reportedState") or {}
    ips = state.get("ipAddresses") or []
    ip = ips[0] if ips else None
    if not ip:
        return None
    # Consoles (UDM/UDR/Dream Machine) serve the controller on 443; CloudKeys on 8443.
    htype = (host.get("type") or "").lower()
    port = 443 if htype == "console" else 8443
    name = state.get("hostname") or host.get("hardwareId") or f"UniFi {host.get('id', '')}"
    return {
        "name": name, "host": ip, "port": port,
        "cloud_host_id": str(host.get("id", "")),
    }


def discover_controllers() -> dict:
    """
    Pull all hosts from the Site Manager API and upsert a UnifiController per host
    (keyed by cloud_host_id). Returns {"discovered": N, "controllers": [...]}.
    Stamps last_sync/last_error/host_count on the cloud account.
    """
    from django.utils import timezone

    from .models import UnifiCloudAccount, UnifiController

    account = UnifiCloudAccount.load()
    api_key = _read_api_key()
    if not api_key:
        account.last_error = "No API key configured"
        account.save(update_fields=["last_error", "updated_at"])
        raise UnifiCloudError("No UniFi Site Manager API key configured")

    try:
        hosts = UnifiCloudClient(api_key).get_hosts()
    except UnifiCloudError as exc:
        account.last_error = str(exc)[:512]
        account.save(update_fields=["last_error", "updated_at"])
        raise

    results: list[dict] = []
    for host in hosts:
        fields = _host_to_controller_fields(host)
        if not fields:
            continue
        state = host.get("reportedState") or {}
        existing = UnifiController.objects.filter(cloud_host_id=fields["cloud_host_id"]).first()
        if existing:
            existing.name = fields["name"]
            existing.host = fields["host"]
            existing.port = fields["port"]
            existing.save(update_fields=["name", "host", "port", "updated_at"])
            status = "updated"
        else:
            UnifiController.objects.create(
                name=fields["name"], host=fields["host"], port=fields["port"],
                cloud_host_id=fields["cloud_host_id"], username="", enabled=True,
            )
            status = "created"
        results.append({
            "name": fields["name"], "host": fields["host"], "port": fields["port"],
            "model": host.get("hardwareId", ""), "version": state.get("version", ""),
            "status": status,
        })

    account.last_sync = timezone.now()
    account.last_error = ""
    account.host_count = len(results)
    account.save(update_fields=["last_sync", "last_error", "host_count", "updated_at"])
    logger.info("UniFi cloud discover: %d controller(s)", len(results))
    return {"discovered": len(results), "controllers": results}
