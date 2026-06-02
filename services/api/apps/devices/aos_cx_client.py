"""
AOS-CX REST API client (Aruba CX switches).

A thin, synchronous wrapper over the AOS-CX REST API (default ``v10.09``), used
by device enrichment. AOS-CX switches expose a full REST API on :443 with
cookie-session auth; the same local username/password used for SSH usually
works for REST too (see the "PINNED — AOS-CX Device Enrichment" spec in
CLAUDE.md).

Defensive by design: callers (enrichment) treat any exception as "REST
unavailable" and fall back to SNMP, then SSH. The client supports the context
manager protocol so the session is always logged out / closed:

    with AOSCXClient(device_ip) as client:
        client.login(username, password)
        info = client.get_system()   # {hostname, version, model, serial, raw}

Self-signed certificates are the norm on AOS-CX, so TLS verification defaults
to off.
"""
from __future__ import annotations

import logging

import requests
import urllib3

logger = logging.getLogger(__name__)

DEFAULT_API_VERSION = "v10.09"
DEFAULT_TIMEOUT = 10  # seconds — per the spec


class AOSCXClient:
    """Cookie-session REST client for a single AOS-CX switch."""

    def __init__(self, ip, *, api_version: str = DEFAULT_API_VERSION,
                 verify_ssl: bool = False, timeout: int = DEFAULT_TIMEOUT):
        self.ip = str(ip)
        self.api_version = api_version
        self.base_url = f"https://{self.ip}/rest/{api_version}"
        self.verify_ssl = verify_ssl
        self.timeout = timeout
        self._session = requests.Session()
        self._session.verify = verify_ssl
        self._logged_in = False
        if not verify_ssl:
            # AOS-CX ships a self-signed cert by default; silence the per-request
            # InsecureRequestWarning so logs aren't flooded during enrichment.
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    # ── context manager ─────────────────────────────────────────────────────
    def __enter__(self) -> "AOSCXClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        self.logout()
        return False  # never suppress exceptions

    # ── auth ────────────────────────────────────────────────────────────────
    def login(self, username: str, password: str) -> dict:
        """
        Authenticate; the session cookie is retained for subsequent calls.
        Returns the cookie jar as a dict. Raises on HTTP error.
        """
        resp = self._session.post(
            self._url("login"),
            data={"username": username, "password": password},
            timeout=self.timeout, verify=self.verify_ssl,
        )
        resp.raise_for_status()
        self._logged_in = True
        return self._session.cookies.get_dict()

    def logout(self) -> None:
        """Best-effort logout + close the underlying session (idempotent)."""
        try:
            if self._logged_in:
                self._session.post(self._url("logout"), timeout=self.timeout,
                                   verify=self.verify_ssl)
        except Exception as exc:  # noqa: BLE001 — logout is best-effort
            logger.debug("AOS-CX logout failed for %s: %s", self.ip, exc)
        finally:
            self._logged_in = False
            self._session.close()

    # ── system info (Stage 1) ────────────────────────────────────────────────
    def get_system(self) -> dict:
        """
        Return normalized system info: ``{hostname, version, model, serial, raw}``.
        ``raw`` is the full decoded ``GET /system`` payload for callers that need
        more than the four common fields.
        """
        data = self._get("system")
        hw = data.get("hardware_info") or {}
        return {
            "hostname": data.get("hostname", "") or "",
            "version": data.get("software_version", "") or "",
            "model": (hw.get("product_name") or data.get("product_name") or ""),
            "serial": data.get("serial_number", "") or "",
            "raw": data,
        }

    # ── interfaces (wired into enrichment in Stage 2) ────────────────────────
    def get_interfaces(self) -> list[dict]:
        """
        Return interfaces as ``[{name, type, admin_state, link_state, ip}]``.
        ``GET /system/interfaces?depth=2`` returns a dict keyed by interface
        name (depth>=2) or a map of name→URI (depth 1); both are handled.
        """
        data = self._get("system/interfaces", params={"depth": 2})
        out: list[dict] = []
        for name, obj in _iter_named(data):
            if not isinstance(obj, dict):
                # depth-1 form (name → URI string): nothing but the name.
                out.append({"name": name, "type": "", "admin_state": "",
                            "link_state": "", "ip": ""})
                continue
            out.append({
                "name": obj.get("name", name) or name,
                "type": obj.get("type", "") or "",
                "admin_state": obj.get("admin_state", "") or "",
                "link_state": obj.get("link_state", "") or "",
                "ip": obj.get("ip4_address", "") or "",
            })
        return out

    # ── LLDP neighbours (wired into enrichment in Stage 2) ───────────────────
    def get_lldp_neighbors(self) -> list[dict]:
        """
        Return LLDP neighbours as
        ``[{local_port, neighbor_hostname, neighbor_port}]``.
        """
        data = self._get("system/lldp_neighbors_info", params={"depth": 2})
        out: list[dict] = []
        for key, obj in _iter_named(data):
            if not isinstance(obj, dict):
                continue
            out.append({
                "local_port": obj.get("port", key) or key,
                "neighbor_hostname": obj.get("neighbor_info", {}).get("chassis_name", "")
                if isinstance(obj.get("neighbor_info"), dict) else "",
                "neighbor_port": obj.get("neighbor_info", {}).get("port_id", "")
                if isinstance(obj.get("neighbor_info"), dict) else "",
            })
        return out

    # ── internals ─────────────────────────────────────────────────────────────
    def _url(self, path: str) -> str:
        return f"{self.base_url}/{path.lstrip('/')}"

    def _get(self, path: str, params: dict | None = None):
        resp = self._session.get(self._url(path), params=params,
                                 timeout=self.timeout, verify=self.verify_ssl)
        resp.raise_for_status()
        return resp.json()


def _iter_named(data):
    """
    Yield (name, obj) pairs from an AOS-CX collection response, which may be a
    dict keyed by name or a list of objects.
    """
    if isinstance(data, dict):
        yield from data.items()
    elif isinstance(data, list):
        for obj in data:
            name = obj.get("name") if isinstance(obj, dict) else None
            yield (name or "", obj)
