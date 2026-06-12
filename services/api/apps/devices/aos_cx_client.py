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
from urllib.parse import unquote

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
            stats = obj.get("statistics")
            rates = obj.get("rate_statistics")
            lacp = obj.get("lacp_status")
            bond = obj.get("bond_status")
            out.append({
                "name": obj.get("name", name) or name,
                "type": obj.get("type", "") or "",
                "admin_state": obj.get("admin_state", "") or "",
                "link_state": obj.get("link_state", "") or "",
                "ip": obj.get("ip4_address", "") or "",
                "description": obj.get("description", "") or "",
                "speed_mbps": _speed_mbps(obj.get("link_speed")),
                "mtu": _int_or_none(obj.get("mtu") or obj.get("active_mtu")),
                # VLAN membership lives on the interface (AOS-CX has no
                # vlans/<id>/ports child); expose the access/trunk config.
                "vlan_mode": obj.get("vlan_mode", "") or "",
                "vlan_tag": _ref_name(obj.get("vlan_tag")),
                "vlan_trunks": _ref_names(obj.get("vlan_trunks")),
                # LACP/LAG state is carried inline on the interface.
                "lacp_status": lacp if isinstance(lacp, dict) else {},
                "bond_status": bond if isinstance(bond, dict) else {},
                "statistics": stats if isinstance(stats, dict) else {},
                "rate_statistics": rates if isinstance(rates, dict) else {},
            })
        return out

    def get_interface_stats(self, port: str) -> dict:
        """
        Return the counter/rate summary for a single interface, mapped onto the
        common schema (see :func:`interface_counters`). ``GET /system/interfaces/
        <port>?depth=2`` scoped to the statistics attributes.
        """
        obj = self._get(f"system/interfaces/{_enc(port)}",
                        params={"depth": 2, "attributes": "name,statistics,rate_statistics"})
        return interface_counters(obj if isinstance(obj, dict) else {})

    def get_poe_status(self) -> list[dict]:
        """
        Return per-port PoE status for PoE-capable interfaces:
        ``[{port, admin_disable, poe_status, power_drawn, power_allocated,
        pd_class}]``.

        AOS-CX exposes PoE under each physical interface's ``poe_interface``
        child (``GET /system/interfaces/<port>/poe_interface``); ports without
        PoE (e.g. SFP+ on a 6300M) return HTTP 404 and are skipped. Returns an
        empty list on a switch with no PoE-capable ports.
        """
        out: list[dict] = []
        for name in self._physical_ports():
            try:
                obj = self._get(f"system/interfaces/{_enc(name)}/poe_interface",
                                params={"depth": 1})
            except Exception:  # noqa: BLE001 — 404 on non-PoE ports
                continue
            row = _parse_poe(name, obj)
            if row:
                out.append(row)
        return out

    def _physical_ports(self) -> list[str]:
        """Physical port names (``1/1/1`` form) — the only PoE-capable ports."""
        try:
            data = self._get("system/interfaces", params={"depth": 1})
        except Exception:  # noqa: BLE001
            return []
        names = list(data.keys()) if isinstance(data, dict) else []
        return [n for n in names if re.fullmatch(r"\d+/\d+/\d+", str(n))]

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

    # ── richer system info (stack serial / base MAC) ─────────────────────────
    def get_system_info(self) -> dict:
        """
        Return the full normalized system identity:
        ``{hostname, os_version, model, serial_number, base_mac, product_name,
        part_number, raw}``.

        ``hostname``/``os_version``/``model`` come straight off ``GET /system``
        (``platform_name`` is the model family, e.g. ``6300``). ``serial_number``
        and ``base_mac`` live on the chassis subsystem's ``product_info`` block —
        on a VSF stack the conductor (first chassis) carries the system identity —
        so they are read from the first ``chassis,*`` subsystem.
        """
        data = self._get("system", params={
            "depth": 1,
            "attributes": "hostname,platform_name,software_version",
        })
        pi = self._primary_chassis_product_info()
        return {
            "hostname": data.get("hostname", "") or "",
            "os_version": data.get("software_version", "") or "",
            "model": data.get("platform_name", "") or "",
            "serial_number": pi.get("serial_number", "") or "",
            "base_mac": pi.get("base_mac_address", "") or "",
            "product_name": pi.get("product_name", "") or "",
            "part_number": pi.get("part_number", "") or "",
            "raw": data,
        }

    def _primary_chassis_product_info(self) -> dict:
        """``product_info`` of the first chassis subsystem (serial + base MAC).

        Returns ``{}`` when the subsystems collection or the chassis resource is
        unavailable so ``get_system_info`` still returns the core fields.
        """
        try:
            subs = self._get("system/subsystems")
        except Exception as exc:  # noqa: BLE001 — identity is best-effort
            logger.debug("AOS-CX %s: subsystems unavailable (%s)", self.ip, exc)
            return {}
        if not isinstance(subs, dict):
            return {}
        chassis_key = next((k for k in subs if str(k).startswith("chassis")), None)
        if not chassis_key:
            return {}
        try:
            obj = self._get(f"system/subsystems/{_enc(chassis_key)}")
        except Exception as exc:  # noqa: BLE001
            logger.debug("AOS-CX %s: chassis %s unavailable (%s)", self.ip, chassis_key, exc)
            return {}
        pi = obj.get("product_info") if isinstance(obj, dict) else None
        return pi if isinstance(pi, dict) else {}

    # ── ARP / neighbour table ─────────────────────────────────────────────────
    def get_arp_table(self) -> list[dict]:
        """
        Return the IPv4 ARP table across all VRFs as normalized rows
        ``[{ip_address, mac_address, interface, vlan, age_minutes, protocol,
        entry_type}]`` (the schema ``apps.arp_mac.store_arp_mac`` persists).

        AOS-CX exposes ARP as the per-VRF ``neighbors`` collection
        (``GET /system/vrfs/<vrf>/neighbors?depth=2``); each entry carries
        ``ip_address``, ``mac``, ``from`` (dynamic/static) and a ``port``
        reference. IPv6 neighbour-discovery entries are skipped (ARP is IPv4).
        MAC addresses are returned as advertised — the caller normalises them.
        """
        out: list[dict] = []
        for vrf in self._list_vrfs():
            try:
                data = self._get(f"system/vrfs/{_enc(vrf)}/neighbors", params={"depth": 2})
            except Exception as exc:  # noqa: BLE001 — VRF may have no neighbours
                logger.debug("AOS-CX %s: neighbors for vrf %s unavailable (%s)",
                             self.ip, vrf, exc)
                continue
            for key, nb in _iter_named(data):
                row = _parse_arp_neighbor(key, nb)
                if row:
                    out.append(row)
        return out

    # ── MAC address table ─────────────────────────────────────────────────────
    def get_mac_table(self) -> list[dict]:
        """
        Return the bridge MAC address-table as normalized rows
        ``[{mac_address, vlan, interface, entry_type}]``.

        AOS-CX keys the forwarding table under each VLAN's ``macs`` child. We
        list the VLANs (``GET /system/vlans?depth=1`` → ``{<vlan_id>: URI}``)
        and fetch each VLAN's table separately (``GET
        /system/vlans/<id>/macs?depth=2``). A single bulk
        ``depth=4&attributes=id,macs`` expansion was tried first, but on busy
        switches it routinely exceeds the request timeout and the whole table
        comes back empty — per-VLAN requests stay well under the limit. Each
        entry is keyed ``<selector>,<mac>`` and carries ``mac_addr``, ``from``
        (dynamic/static) and a ``port`` ref. Returns ``[]`` if the table is
        empty or the VLAN list is unreachable.
        """
        out: list[dict] = []
        try:
            vlans = self._get("system/vlans", params={"depth": 1})
        except Exception as exc:  # noqa: BLE001 — no VLAN list → no MAC table
            logger.warning("AOS-CX %s: vlan list unavailable (%s)", self.ip, exc)
            return out
        if not isinstance(vlans, dict):
            return out
        for vkey, _uri in vlans.items():
            vid = _int_or_none(vkey)
            try:
                macs = self._get(f"system/vlans/{_enc(str(vkey))}/macs",
                                 params={"depth": 2})
            except Exception as exc:  # noqa: BLE001 — skip a single bad VLAN
                logger.debug("AOS-CX %s: mac table for VLAN %s unavailable (%s)",
                             self.ip, vkey, exc)
                continue
            if not isinstance(macs, dict):
                continue
            for mkey, m in macs.items():
                row = _parse_mac_entry(vid, mkey, m)
                if row:
                    out.append(row)
        logger.info("AOS-CX %s: collected %d MAC entries across %d VLANs (REST)",
                    self.ip, len(out), len(vlans))
        return out

    # ── environment / sensors ─────────────────────────────────────────────────
    def get_environment(self) -> dict:
        """
        Return temperature/fan/PSU sensors aggregated across every subsystem:
        ``{temperatures: [...], fans: [...], power_supplies: [...]}``.

        A single ``GET /system/subsystems?depth=4`` (scoped to the sensor
        children) expands each member's ``temp_sensors``/``fans``/
        ``power_supplies`` inline. AOS-CX reports temperatures in milli-degrees
        Celsius (61375 → 61.375 °C). Returns empty lists on any error.
        """
        env = {"temperatures": [], "fans": [], "power_supplies": []}
        try:
            data = self._get("system/subsystems", params={
                "depth": 4,
                "attributes": "name,type,temp_sensors,fans,power_supplies",
            })
        except Exception as exc:  # noqa: BLE001 — environment is best-effort
            logger.debug("AOS-CX %s: subsystems environment unavailable (%s)", self.ip, exc)
            return env
        if not isinstance(data, dict):
            return env
        for sub_name, sub in data.items():
            if not isinstance(sub, dict):
                continue
            for sname, s in _iter_named(sub.get("temp_sensors") or {}):
                if isinstance(s, dict):
                    env["temperatures"].append({
                        "name": s.get("name") or sname,
                        "subsystem": sub_name,
                        "location": s.get("location", "") or "",
                        "temperature_c": _milli_c(s.get("temperature")),
                        "status": s.get("status", "") or "",
                    })
            for fname, f in _iter_named(sub.get("fans") or {}):
                if isinstance(f, dict):
                    env["fans"].append({
                        "name": f.get("name") or fname,
                        "subsystem": sub_name,
                        "rpm": _int_or_none(f.get("rpm")),
                        "speed": f.get("speed", "") or "",
                        "status": f.get("status", "") or "",
                    })
            for pname, p in _iter_named(sub.get("power_supplies") or {}):
                if isinstance(p, dict):
                    char = p.get("characteristics") or {}
                    ident = p.get("identity") or {}
                    env["power_supplies"].append({
                        "name": p.get("name") or pname,
                        "subsystem": sub_name,
                        "status": p.get("status", "") or "",
                        "instantaneous_power": _int_or_none(char.get("instantaneous_power")),
                        "maximum_power": _int_or_none(char.get("maximum_power")),
                        "model": ident.get("product_name", "") or "",
                        "serial": ident.get("serial_number", "") or "",
                    })
        return env

    # ── VLANs ─────────────────────────────────────────────────────────────────
    def get_vlans(self) -> list[dict]:
        """Return VLANs as ``[{id, name, admin, oper_state, type, description}]``
        from ``GET /system/vlans?depth=2`` (keyed by VLAN id)."""
        out: list[dict] = []
        data = self._get("system/vlans", params={"depth": 2})
        for key, v in _iter_named(data):
            if not isinstance(v, dict):
                out.append({"id": _int_or_none(key), "name": "", "admin": "",
                            "oper_state": "", "type": "", "description": ""})
                continue
            out.append({
                "id": v.get("id") if v.get("id") is not None else _int_or_none(key),
                "name": v.get("name", "") or "",
                "admin": v.get("admin", "") or "",
                "oper_state": v.get("oper_state", "") or "",
                "type": v.get("type", "") or "",
                "description": v.get("description", "") or "",
            })
        return out

    # ── routes (lower priority) ───────────────────────────────────────────────
    def get_routes(self) -> list[dict]:
        """Return IPv4/IPv6 routes across all VRFs as
        ``[{prefix, vrf, address_family, protocol}]`` (best-effort)."""
        out: list[dict] = []
        for vrf in self._list_vrfs():
            try:
                data = self._get(f"system/vrfs/{_enc(vrf)}/routes", params={"depth": 2})
            except Exception as exc:  # noqa: BLE001
                logger.debug("AOS-CX %s: routes for vrf %s unavailable (%s)",
                             self.ip, vrf, exc)
                continue
            for key, r in _iter_named(data):
                if not isinstance(r, dict):
                    out.append({"prefix": unquote(str(key)), "vrf": vrf,
                                "address_family": "", "protocol": ""})
                    continue
                out.append({
                    "prefix": r.get("prefix") or unquote(str(key)),
                    "vrf": vrf,
                    "address_family": r.get("address_family", "") or "",
                    "protocol": r.get("from", "") or "",
                })
        return out

    # ── unified collection ────────────────────────────────────────────────────
    def collect_all(self) -> dict:
        """
        Collect everything AOS-CX exposes over REST in one authenticated session:
        ``{system, interfaces, arp, mac, environment, vlans, lldp}``. Each piece
        is best-effort — a failure in one section yields an empty value for it
        rather than aborting the whole collection.
        """
        def _safe(fn, default):
            try:
                return fn()
            except Exception as exc:  # noqa: BLE001
                logger.warning("AOS-CX %s: %s failed: %s", self.ip, fn.__name__, exc)
                return default

        return {
            "system": _safe(self.get_system_info, {}),
            "interfaces": _safe(self.get_interfaces, []),
            "arp": _safe(self.get_arp_table, []),
            "mac": _safe(self.get_mac_table, []),
            "environment": _safe(self.get_environment,
                                 {"temperatures": [], "fans": [], "power_supplies": []}),
            "vlans": _safe(self.get_vlans, []),
            "lldp": _safe(self.get_lldp_neighbors, []),
        }

    def _list_vrfs(self) -> list[str]:
        """VRF names from ``GET /system/vrfs`` (falls back to ``['default']``)."""
        try:
            data = self._get("system/vrfs")
        except Exception as exc:  # noqa: BLE001
            logger.debug("AOS-CX %s: vrfs unavailable (%s)", self.ip, exc)
            return ["default"]
        if isinstance(data, dict) and data:
            return list(data.keys())
        return ["default"]

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


def _enc(key: str) -> str:
    """
    Encode an AOS-CX resource key for use as a URL path segment. Slashes inside
    interface/subsystem names (e.g. ``line_card,1/1``) must be percent-encoded
    so they aren't read as path separators; commas are left as-is (AOS-CX returns
    them unencoded in its own URIs).
    """
    return str(key).replace("/", "%2F")


def _int_or_none(value):
    """Best-effort int (handles AOS-CX numeric strings); None when unparseable."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _milli_c(value):
    """AOS-CX reports sensor temperature in milli-degrees C (61375 → 61.375)."""
    v = _int_or_none(value)
    return round(v / 1000.0, 3) if v is not None else None


def _ref_name(ref) -> str:
    """
    Resolve an AOS-CX resource reference to a bare name. References come as a
    ``{name: URI}`` dict (depth ≥ 2) or a plain URI string; either way we want
    the interface/VLAN name (the dict key, or the URI's last decoded segment).
    """
    if isinstance(ref, dict) and ref:
        return str(next(iter(ref.keys())))
    if isinstance(ref, str) and ref:
        return unquote(ref.rstrip("/").rsplit("/", 1)[-1])
    return ""


def _ref_names(ref) -> list[str]:
    """
    List of names from an AOS-CX reference collection — a ``{name: URI}`` dict
    or a list of refs/URIs (e.g. an interface's ``vlan_trunks``). Empty when
    absent.
    """
    if isinstance(ref, dict):
        return [str(k) for k in ref]
    if isinstance(ref, list):
        out = []
        for x in ref:
            if isinstance(x, dict) and x:
                out.append(str(next(iter(x))))
            elif isinstance(x, str) and x:
                out.append(unquote(x.rstrip("/").rsplit("/", 1)[-1]))
        return out
    return []


def _parse_poe(port: str, obj) -> dict | None:
    """
    Normalise an AOS-CX ``poe_interface`` payload. Fields are read with
    fallbacks because the layout differs across firmware (some nest under
    ``config``/``power``/``status``, others keep them at the top level).
    """
    if not isinstance(obj, dict):
        return None
    status = obj.get("status") if isinstance(obj.get("status"), dict) else {}
    power = obj.get("power") if isinstance(obj.get("power"), dict) else {}
    config = obj.get("config") if isinstance(obj.get("config"), dict) else {}
    return {
        "port": port,
        "admin_disable": bool(config.get("admin_disable", obj.get("admin_disable", False))),
        "poe_status": (status.get("poe_oper_status") or obj.get("poe_status")
                       or status.get("status") or ""),
        "power_drawn": _int_or_none(status.get("power_drawn_in_watts")
                                    or power.get("power_drawn") or obj.get("power_drawn")),
        "power_allocated": _int_or_none(status.get("power_allocated_in_watts")
                                        or power.get("power_allocated")
                                        or obj.get("power_allocated")),
        "pd_class": (status.get("pd_class_actual") or status.get("pd_class")
                     or obj.get("pd_class") or ""),
    }


def _vlan_from_ifname(name: str):
    """VLAN id from an SVI interface name (``vlan20`` → 20); None otherwise."""
    m = re.fullmatch(r"vlan(\d+)", str(name or "").strip(), re.IGNORECASE)
    return int(m.group(1)) if m else None


def _parse_arp_neighbor(key, nb) -> dict | None:
    """Normalise one AOS-CX ``neighbors`` entry into an ARP row (IPv4 only)."""
    if not isinstance(nb, dict):
        return None
    if (nb.get("address_family") or "ipv4").lower() != "ipv4":
        return None  # IPv6 ND entries are not ARP
    ip = nb.get("ip_address") or str(key).split(",", 1)[0]
    mac = nb.get("mac") or ""
    if not ip or not mac or not valid_ip(ip):
        return None
    iface = _ref_name(nb.get("port")) or _ref_name(nb.get("phy_port"))
    return {
        "ip_address": ip,
        "mac_address": mac,
        "interface": iface,
        "vlan": _vlan_from_ifname(iface),
        "age_minutes": None,   # AOS-CX neighbours carry no age
        "protocol": "Internet",
        "entry_type": "static" if (nb.get("from") or "").lower() in ("static", "permanent")
        else "dynamic",
    }


def _parse_mac_entry(vid, key, m) -> dict | None:
    """Normalise one AOS-CX VLAN ``macs`` entry (keyed ``<selector>,<mac>``)."""
    parts = str(key).split(",")
    key_mac = parts[-1] if len(parts) >= 2 else ""
    key_sel = parts[0] if len(parts) >= 2 else ""
    if isinstance(m, dict):
        mac = m.get("mac_addr") or key_mac
        iface = _ref_name(m.get("port"))
        sel = m.get("from") or key_sel or "dynamic"
    else:
        mac, iface, sel = key_mac, "", (key_sel or "dynamic")
    if not mac:
        return None
    return {
        "mac_address": mac,
        "vlan": _int_or_none(vid),
        "interface": iface,
        "entry_type": str(sel).lower(),
    }


def interface_counters(iface: dict) -> dict:
    """
    Map an AOS-CX interface (from :meth:`AOSCXClient.get_interfaces`) onto the
    common interface-counter schema. ``statistics`` holds cumulative counters and
    ``rate_statistics`` the device-computed per-second rates — preferring the
    latter avoids re-deriving rates from counter deltas. Missing counters
    (e.g. errors aren't exposed on every firmware) come back as ``None``.
    """
    st = iface.get("statistics") or {}
    rt = iface.get("rate_statistics") or {}

    def pick(src, *keys):
        for k in keys:
            if src.get(k) is not None:
                return _int_or_none(src.get(k))
        return None

    return {
        "rx_bytes": pick(st, "rx_bytes", "if_hc_in_bytes"),
        "tx_bytes": pick(st, "tx_bytes", "if_hc_out_bytes"),
        "rx_packets": pick(st, "rx_packets", "if_hc_in_unicast_packets"),
        "tx_packets": pick(st, "tx_packets", "if_hc_out_unicast_packets"),
        "rx_errors": pick(st, "if_in_errors", "rx_errors"),
        "tx_errors": pick(st, "if_out_errors", "tx_errors"),
        "rx_discards": pick(st, "fe_if_in_discard_packets", "if_in_discards", "rx_dropped"),
        "tx_discards": pick(st, "if_out_discards", "tx_dropped"),
        "rx_bps": pick(rt, "rx_bytes_per_second"),
        "tx_bps": pick(rt, "tx_bytes_per_second"),
        "rx_pps": pick(rt, "rx_packets_per_second"),
        "tx_pps": pick(rt, "tx_packets_per_second"),
    }
