"""
Interface discovery.

Discovers a device's interfaces via SNMP (walks ifTable / ifXTable / lldpRemTable)
or, when no SNMP credential is configured, via SSH (Netmiko + TextFSM). Heavy
libraries (pysnmp, netmiko) are imported lazily, and the protocol-specific
functions (``_discover_via_snmp`` / ``_discover_via_ssh``) are the seams tests
monkeypatch. ``should_auto_select`` is a pure helper.
"""
from __future__ import annotations

import asyncio
import logging

from apps.credentials import vault

logger = logging.getLogger(__name__)


class DiscoveryError(Exception):
    """Raised when discovery cannot run (no usable credential, unreachable, …)."""


# ── OIDs ──────────────────────────────────────────────────────────────────────
OID_IF_DESCR = "1.3.6.1.2.1.2.2.1.2"
OID_IF_TYPE = "1.3.6.1.2.1.2.2.1.3"
OID_IF_SPEED = "1.3.6.1.2.1.2.2.1.5"
OID_IF_ADMIN = "1.3.6.1.2.1.2.2.1.7"
OID_IF_OPER = "1.3.6.1.2.1.2.2.1.8"
OID_IF_NAME = "1.3.6.1.2.1.31.1.1.1.1"
OID_IF_ALIAS = "1.3.6.1.2.1.31.1.1.1.18"
OID_IF_HIGHSPEED = "1.3.6.1.2.1.31.1.1.1.15"
OID_LLDP_REM_SYSNAME = "1.0.8802.1.1.2.1.4.1.1.9"
OID_LLDP_REM_PORTDESC = "1.0.8802.1.1.2.1.4.1.1.8"

_OPER_MAP = {"1": "up", "2": "down", "3": "testing"}


def should_auto_select(iface: dict) -> bool:
    """Smart default selection: real, up, described/connected access interfaces."""
    name = (iface.get("if_name") or "").strip()
    if not name:
        return False
    low = name.lower()
    if low.startswith(("lo", "tu", "nu")):  # loopback / tunnel / null
        return False
    itype = (iface.get("if_type") or "").lower()
    if any(k in itype for k in ("loopback", "tunnel", "null")):
        return False
    up = (iface.get("oper_status") or "").lower() == "up"
    has_context = bool((iface.get("if_description") or "").strip()) or bool(iface.get("lldp_neighbor_hostname"))
    return up and has_context


def discover_interfaces(device) -> list[dict]:
    """
    Discover interfaces for a device. Returns a list of dicts with auto_select +
    collection_method annotated. Raises DiscoveryError on unusable config.
    """
    profile = device.credential_profile
    if not profile:
        raise DiscoveryError("device has no credential profile")
    creds = vault.read_secret(profile.vault_path) if profile.vault_path else {}
    host = str(device.management_ip or device.ip_address)
    collection_method = "gnmi" if profile.gnmi_enabled else "snmp"

    if profile.snmpv2c_enabled:
        community = creds.get("snmpv2c_community") or "public"
        raw = _discover_via_snmp(host, community, profile.snmpv2c_port or 161)
    elif profile.ssh_enabled:
        raw = _discover_via_ssh(device, profile, creds)
    elif profile.snmpv3_enabled:
        raise DiscoveryError("SNMPv3 discovery is not implemented yet")
    else:
        raise DiscoveryError("profile has neither SNMP v2c nor SSH enabled")

    for r in raw:
        r["auto_select"] = should_auto_select(r)
        r["collection_method"] = collection_method
    return raw


# ── SNMP ──────────────────────────────────────────────────────────────────────

def _discover_via_snmp(host: str, community: str, port: int) -> list[dict]:
    try:
        return asyncio.run(_snmp_walk_all(host, community, port))
    except DiscoveryError:
        raise
    except Exception as exc:
        raise DiscoveryError(f"SNMP discovery failed: {exc}") from exc


async def _snmp_walk_column(host: str, port: int, community: str, base_oid: str) -> dict[str, str]:
    """Walk one OID column; return {index_suffix: value}."""
    from pysnmp.hlapi.v3arch.asyncio import (
        CommunityData, ContextData, ObjectIdentity, ObjectType, SnmpEngine,
        UdpTransportTarget, bulk_walk_cmd,
    )
    out: dict[str, str] = {}
    engine = SnmpEngine()
    target = await UdpTransportTarget.create((host, port), timeout=3, retries=1)
    objects = bulk_walk_cmd(
        engine, CommunityData(community, mpModel=1), target, ContextData(),
        0, 25, ObjectType(ObjectIdentity(base_oid)), lexicographicMode=False,
    )
    prefix = base_oid + "."
    async for err_ind, err_stat, _err_idx, var_binds in objects:
        if err_ind:
            raise DiscoveryError(str(err_ind))
        if err_stat:
            raise DiscoveryError(err_stat.prettyPrint())
        for oid, val in var_binds:
            soid = str(oid)
            if not soid.startswith(prefix):
                return out  # walked past the column
            out[soid[len(prefix):]] = val.prettyPrint()
    return out


async def _snmp_walk_all(host: str, community: str, port: int) -> list[dict]:
    descr = await _snmp_walk_column(host, port, community, OID_IF_DESCR)
    if not descr:
        raise DiscoveryError("no interfaces returned (check SNMP community / reachability)")
    itype = await _snmp_walk_column(host, port, community, OID_IF_TYPE)
    speed = await _snmp_walk_column(host, port, community, OID_IF_SPEED)
    admin = await _snmp_walk_column(host, port, community, OID_IF_ADMIN)
    oper = await _snmp_walk_column(host, port, community, OID_IF_OPER)
    name = await _snmp_walk_column(host, port, community, OID_IF_NAME)
    alias = await _snmp_walk_column(host, port, community, OID_IF_ALIAS)
    hispeed = await _snmp_walk_column(host, port, community, OID_IF_HIGHSPEED)
    lldp_name = await _snmp_walk_column(host, port, community, OID_LLDP_REM_SYSNAME)
    lldp_port = await _snmp_walk_column(host, port, community, OID_LLDP_REM_PORTDESC)

    # lldp index = timeMark.localPortNum.remIndex — map by localPortNum (≈ ifIndex).
    def lldp_by_port(col: dict) -> dict[str, str]:
        m = {}
        for idx, v in col.items():
            parts = idx.split(".")
            if len(parts) >= 2:
                m[parts[1]] = v
        return m

    lldp_name_by_port = lldp_by_port(lldp_name)
    lldp_port_by_port = lldp_by_port(lldp_port)

    rows = []
    for if_index in descr:
        hi = int(hispeed.get(if_index, "0") or 0)
        sp = int(speed.get(if_index, "0") or 0)
        speed_mbps = hi if hi > 0 else (sp // 1_000_000 if sp else None)
        rows.append({
            "if_index": int(if_index) if if_index.isdigit() else None,
            "if_name": name.get(if_index) or descr.get(if_index, ""),
            "if_description": alias.get(if_index, ""),
            "if_speed_mbps": speed_mbps,
            "if_type": itype.get(if_index, ""),
            "oper_status": _OPER_MAP.get(oper.get(if_index, ""), "unknown"),
            "admin_status": _OPER_MAP.get(admin.get(if_index, ""), "unknown"),
            "lldp_neighbor_hostname": lldp_name_by_port.get(if_index) or None,
            "lldp_neighbor_port": lldp_port_by_port.get(if_index) or None,
            "lldp_neighbor_desc": lldp_port_by_port.get(if_index) or None,
        })
    return rows


# ── SSH (fallback) ────────────────────────────────────────────────────────────

def _discover_via_ssh(device, profile, creds: dict) -> list[dict]:
    from netmiko import ConnectHandler
    from apps.compliance.collector import netmiko_device_type

    host = str(device.management_ip or device.ip_address)
    params = {
        "device_type": netmiko_device_type(device.vendor, device.platform),
        "host": host,
        "username": profile.ssh_username,
        "password": creds.get("ssh_password", ""),
        "port": profile.ssh_port or 22,
        "fast_cli": False,
    }
    if params["device_type"] == "autodetect":
        params["device_type"] = "cisco_ios"

    try:
        conn = ConnectHandler(**params)
    except Exception as exc:
        raise DiscoveryError(f"SSH connection failed: {exc}") from exc
    try:
        intf = conn.send_command("show interfaces", use_textfsm=True)
        try:
            lldp = conn.send_command("show lldp neighbors detail", use_textfsm=True)
        except Exception:
            lldp = []
    finally:
        conn.disconnect()

    if not isinstance(intf, list):
        raise DiscoveryError("could not parse 'show interfaces' output")

    lldp_map: dict[str, dict] = {}
    if isinstance(lldp, list):
        for n in lldp:
            n = {k.lower(): v for k, v in n.items()}
            local = _norm(n.get("local_interface") or n.get("local_port") or "")
            if local:
                lldp_map[local] = {
                    "host": n.get("neighbor") or n.get("neighbor_name") or n.get("system_name") or "",
                    "port": n.get("neighbor_interface") or n.get("neighbor_port_id") or n.get("port_id") or "",
                    "desc": n.get("neighbor_port_description") or n.get("port_description") or "",
                }

    rows = []
    for row in intf:
        r = {k.lower(): v for k, v in row.items()}
        if_name = r.get("interface") or r.get("port") or ""
        if not if_name:
            continue
        link = (r.get("link_status") or r.get("status") or "").lower()
        oper = "up" if "up" in link and "down" not in link else "down"
        admin = "down" if "administratively" in link else "up"
        bw = r.get("bandwidth") or r.get("speed") or ""
        rows.append({
            "if_index": None,
            "if_name": if_name,
            "if_description": r.get("description", "") or "",
            "if_speed_mbps": _bandwidth_to_mbps(bw),
            "if_type": r.get("hardware_type", "") or "",
            "oper_status": oper,
            "admin_status": admin,
            "lldp_neighbor_hostname": (lldp_map.get(_norm(if_name)) or {}).get("host") or None,
            "lldp_neighbor_port": (lldp_map.get(_norm(if_name)) or {}).get("port") or None,
            "lldp_neighbor_desc": (lldp_map.get(_norm(if_name)) or {}).get("desc") or None,
        })
    return rows


def _norm(name: str) -> str:
    return (name or "").replace(" ", "").lower()


def _bandwidth_to_mbps(bw: str) -> int | None:
    """Parse strings like '1000000 Kbit' or '10000000' (Kbit) into Mbps."""
    if not bw:
        return None
    digits = "".join(c for c in str(bw) if c.isdigit())
    if not digits:
        return None
    kbit = int(digits)
    return kbit // 1000 if kbit else None
