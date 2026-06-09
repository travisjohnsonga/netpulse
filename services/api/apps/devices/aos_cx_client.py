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
import re

import requests
import urllib3

from .lldp import valid_ip

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
        Return interfaces as
        ``[{name, type, admin_state, link_state, ip, description, speed_mbps}]``.
        ``GET /system/interfaces?depth=2`` returns a dict keyed by interface
        name (depth>=2) or a map of name→URI (depth 1); both are handled.
        """
        data = self._get("system/interfaces", params={"depth": 2})
        out: list[dict] = []
        for name, obj in _iter_named(data):
            if not isinstance(obj, dict):
                # depth-1 form (name → URI string): nothing but the name.
                out.append({"name": name, "type": "", "admin_state": "",
                            "link_state": "", "ip": "", "description": "",
                            "speed_mbps": None})
                continue
            out.append({
                "name": obj.get("name", name) or name,
                "type": obj.get("type", "") or "",
                "admin_state": obj.get("admin_state", "") or "",
                "link_state": obj.get("link_state", "") or "",
                "ip": obj.get("ip4_address", "") or "",
                "description": obj.get("description", "") or "",
                "speed_mbps": _speed_mbps(obj.get("link_speed")),
            })
        return out

    # ── LLDP neighbours (wired into enrichment in Stage 2) ───────────────────
    def get_lldp_neighbors(self) -> list[dict]:
        """
        Return LLDP neighbours with every TLV AOS-CX advertises:
        ``[{local_port, neighbor_hostname, neighbor_port,
            neighbor_port_description, neighbor_mgmt_ip, chassis_id,
            chassis_id_type, system_description, capabilities}]``.

        Two firmware-dependent API shapes are supported transparently:

        * **Aggregated** — ``GET /system/lldp_neighbors_info`` returns every
          neighbour in one call (later firmware).
        * **Per-interface walk** — AOS-CX FL.10.13 and earlier do NOT expose
          that top-level collection (it returns HTTP 400); the neighbours are
          only reachable under each interface's ``lldp_neighbors`` child
          resource. We fall back to walking the interfaces when the aggregated
          endpoint errors or returns nothing.

        AOS-CX nests the advertised TLVs under ``neighbor_info``; field names
        differ slightly across firmware (e.g. ``chassis_name`` vs ``system_name``,
        ``chassis_description`` vs ``system_description``), so each value is read
        with fallbacks. ``capabilities`` is returned raw (AOS-CX gives a dict
        keyed by capability, a list, or a delimited string) and normalised
        downstream by ``topology.discover_links`` via ``lldp.normalize_capabilities``.
        """
        try:
            data = self._get("system/lldp_neighbors_info", params={"depth": 2})
        except Exception as exc:  # noqa: BLE001 — endpoint absent on FL.10.13
            logger.debug("AOS-CX %s: lldp_neighbors_info unavailable (%s); "
                         "falling back to per-interface walk", self.ip, exc)
            data = None
        if data:
            return self._parse_lldp_neighbors_info(data)
        return self._get_lldp_via_interfaces()

    def _parse_lldp_neighbors_info(self, data) -> list[dict]:
        """Normalise the aggregated ``system/lldp_neighbors_info`` payload."""
        out: list[dict] = []
        for key, obj in _iter_named(data):
            if not isinstance(obj, dict):
                continue
            info = obj.get("neighbor_info")
            if not isinstance(info, dict):
                info = {}
            out.append({
                "local_port": obj.get("port") or _last_segment(key),
                "neighbor_hostname": (
                    info.get("chassis_name") or info.get("system_name") or ""),
                "neighbor_port": (
                    info.get("port_id") or obj.get("port_id") or ""),
                "neighbor_port_description": info.get("port_description", "") or "",
                "neighbor_mgmt_ip": _first_mgmt_ip(info),
                "chassis_id": info.get("chassis_id", "") or "",
                "chassis_id_type": (
                    info.get("chassis_id_subtype")
                    or info.get("chassis_id_type") or ""),
                "system_description": (
                    info.get("chassis_description")
                    or info.get("system_description") or ""),
                "capabilities": (
                    info.get("chassis_capability_available")
                    or info.get("capabilities")
                    or info.get("system_capabilities") or ""),
            })
        return out

    def _get_lldp_via_interfaces(self) -> list[dict]:
        """
        Collect LLDP neighbours by walking the interfaces tree — used when the
        aggregated ``lldp_neighbors_info`` collection is absent (AOS-CX FL.10.13
        and earlier return HTTP 400 for it).

        On those firmwares LLDP data lives under each interface's
        ``lldp_neighbors`` child. A single
        ``GET /system/interfaces?depth=4&attributes=name,lldp_neighbors``
        expands that child all the way to the per-neighbour detail, so the
        whole table comes back in one request — only ports that actually have a
        neighbour carry data, keeping the response small. (Verified on HPE 6100
        / FL.10.13.) Returns an empty list if the payload shape is unexpected.
        """
        out: list[dict] = []
        data = self._get("system/interfaces",
                         params={"depth": 4, "attributes": "name,lldp_neighbors"})
        if not isinstance(data, dict):
            return out
        for port_name, iface in data.items():
            if not isinstance(iface, dict):
                continue
            neighbors = iface.get("lldp_neighbors")
            if not isinstance(neighbors, dict):
                continue
            local_port = iface.get("name") or port_name
            for detail in neighbors.values():
                parsed = self._parse_neighbor(local_port, detail)
                if parsed:
                    out.append(parsed)
        return out

    def _parse_neighbor(self, local_port: str, data) -> dict | None:
        """
        Normalise one per-interface ``lldp_neighbors`` detail response. The
        per-interface form carries ``chassis_id``/``port_id`` at the top level
        and the remaining TLVs under ``neighbor_info``.
        """
        if not isinstance(data, dict):
            return None
        info = data.get("neighbor_info")
        if not isinstance(info, dict):
            info = {}
        return {
            "local_port": local_port,
            "neighbor_hostname": (
                info.get("chassis_name") or info.get("system_name") or ""),
            "neighbor_port": (
                data.get("port_id") or info.get("port_id") or ""),
            "neighbor_port_description": info.get("port_description", "") or "",
            "neighbor_mgmt_ip": _first_mgmt_ip(info),
            "chassis_id": (
                data.get("chassis_id") or info.get("chassis_id") or ""),
            "chassis_id_type": (
                info.get("chassis_id_subtype")
                or info.get("chassis_id_type") or ""),
            "system_description": (
                info.get("chassis_description")
                or info.get("system_description") or ""),
            "capabilities": (
                info.get("chassis_capability_available")
                or info.get("chassis_capability_enabled")
                or info.get("capabilities") or ""),
        }

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


def _speed_mbps(link_speed):
    """
    Normalize AOS-CX ``link_speed`` to Mbps. AOS-CX reports it in bits/sec
    (e.g. 1_000_000_000 for 1G); values that already look like Mbps are passed
    through. Returns None when absent/unparseable.
    """
    try:
        val = int(link_speed)
    except (TypeError, ValueError):
        return None
    if val <= 0:
        return None
    return val // 1_000_000 if val >= 1_000_000 else val


def _first_mgmt_ip(info: dict) -> str:
    """
    Best management IP from an AOS-CX ``neighbor_info`` block.

    AOS-CX may advertise a single ``mgmt_ip`` or a delimited ``mgmt_ip_list``,
    and some neighbours put a MAC there instead of an IP; each candidate is
    validated so only a real address is returned. Falls back to ``chassis_id``
    only when it itself is an IP (it is usually a MAC, which must never land in
    the ``management_address`` inet column).
    """
    raw = info.get("mgmt_ip") or info.get("mgmt_ip_list") or ""
    for cand in re.split(r"[,;\s]+", str(raw)):
        cand = cand.strip()
        if cand and valid_ip(cand):
            return cand
    cid = str(info.get("chassis_id") or "").strip()
    return cid if valid_ip(cid) else ""


def _last_segment(key: str) -> str:
    """
    The local port for an LLDP entry. AOS-CX keys this collection by the local
    interface name, sometimes as a composite "<port>,<chassis>,<port_id>"; the
    local port is the leading segment.
    """
    return str(key).split(",", 1)[0]
