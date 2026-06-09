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


def _has_network_controller(host: dict) -> bool:
    """
    True only when the host runs a Network controller. Hosts that run only
    Protect/Access/Talk (no Network) can't import devices, so we skip them.
    """
    state = host.get("reportedState") or {}
    for ctrl in state.get("controllers") or []:
        if ctrl.get("name") == "network" and ctrl.get("isRunning"):
            return True
    return False


def _host_to_controller_fields(host: dict) -> dict | None:
    """
    Map a Site Manager host record to UnifiController fields. Returns None when
    the host has no usable management IP.

    The Site Manager payload reports the management IP under ``reportedState.ip``
    (primary LAN IP), with ``ipAddrs`` (list) and ``wans[].ipv4`` as fallbacks —
    NOT ``ipAddresses`` as an earlier version assumed.
    """
    state = host.get("reportedState") or {}
    hardware = state.get("hardware") or {}

    # Management IP, in priority order: primary IP → first IPv4 in ipAddrs →
    # first WAN IPv4.
    ip = state.get("ip") or None
    if not ip:
        for addr in state.get("ipAddrs") or []:
            if ":" not in addr:  # skip IPv6 (incl. link-local)
                ip = addr
                break
    if not ip:
        for wan in state.get("wans") or []:
            if wan.get("ipv4"):
                ip = wan["ipv4"]
                break
    if not ip:
        return None

    # Prefer the controller's reported mgmt port; else consoles (UDM/UDR/Dream
    # Machine) serve on 443 and CloudKeys on 8443.
    htype = (host.get("type") or "").lower()
    port = state.get("mgmt_port") or (443 if htype == "console" else 8443)
    name = (state.get("hostname") or state.get("name") or hardware.get("name")
            or host.get("hardwareId", "")[:32] or f"UniFi {host.get('id', '')[:8]}")
    model = hardware.get("name") or hardware.get("shortname") or ""
    return {
        "name": name, "host": ip, "port": port,
        "cloud_host_id": str(host.get("id", "")),
        "model": model, "version": state.get("version", ""),
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
        if not _has_network_controller(host):
            logger.debug("Skipping host %s — no running network controller", host.get("id"))
            continue
        fields = _host_to_controller_fields(host)
        if not fields:
            continue
        existing = UnifiController.objects.filter(cloud_host_id=fields["cloud_host_id"]).first()
        if existing:
            existing.name = fields["name"]
            existing.host = fields["host"]
            existing.port = fields["port"]
            existing.model = fields["model"]
            existing.version = fields["version"]
            existing.save(update_fields=["name", "host", "port", "model", "version", "updated_at"])
            status = "updated"
        else:
            UnifiController.objects.create(
                name=fields["name"], host=fields["host"], port=fields["port"],
                cloud_host_id=fields["cloud_host_id"], username="", enabled=True,
                model=fields["model"], version=fields["version"],
            )
            status = "created"
        results.append({
            "name": fields["name"], "host": fields["host"], "port": fields["port"],
            "model": fields["model"], "version": fields["version"],
            "status": status,
        })

    account.last_sync = timezone.now()
    account.last_error = ""
    account.host_count = len(results)
    account.save(update_fields=["last_sync", "last_error", "host_count", "updated_at"])
    logger.info("UniFi cloud discover: %d controller(s)", len(results))
    return {"discovered": len(results), "controllers": results}
