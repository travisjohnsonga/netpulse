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


# Interface names/types that are never auto-selected for traffic monitoring.
_EXCLUDE_TOKENS = ("loopback", "tunnel", "null", "management", "mgmt")


def should_auto_select(iface: dict) -> bool:
    """
    Smart default selection for traffic monitoring: auto-select network-to-
    network links — an interface that is operationally UP and has an LLDP
    neighbour, and is not a loopback/tunnel/null/management interface.

    LLDP-neighbour-driven on purpose: edge/access ports (no neighbour) and
    down/virtual/management interfaces are left for the engineer to opt in.
    """
    name = (iface.get("if_name") or "").strip()
    if not name:
        return False
    low = name.lower()
    if low.startswith(("lo", "tu", "nu")):  # short forms: Lo0 / Tu1 / Nu0
        return False
    if any(tok in low for tok in _EXCLUDE_TOKENS):
        return False
    itype = (iface.get("if_type") or "").lower()
    if any(k in itype for k in ("loopback", "tunnel", "null")):
        return False
    up = (iface.get("oper_status") or "").lower() == "up"
    return up and bool(iface.get("lldp_neighbor_hostname"))


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
            "lldp_neighbor_mgmt_ip": None,
        })
    return rows


# ── SSH (TextFSM via ntc-templates) ───────────────────────────────────────────

# netmiko device_type → ntc-templates platform for LLDP parsing. IOS-XE shares
# the cisco_ios templates; IOS-XR uses cisco_xr.
LLDP_NTC_PLATFORM = {
    "cisco_ios":     "cisco_ios",
    "cisco_xe":      "cisco_ios",
    "cisco_nxos":    "cisco_nxos",
    "cisco_xr":      "cisco_xr",
    "juniper_junos": "juniper_junos",
    "arista_eos":    "arista_eos",
}
INTERFACES_NTC_PLATFORM = dict(LLDP_NTC_PLATFORM)


def _ntc_parse(platform: str, command: str, raw: str) -> list[dict]:
    """Parse raw show-command output with ntc-templates; [] if no template/match."""
    try:
        from ntc_templates.parse import parse_output
        return parse_output(platform=platform, command=command, data=raw)
    except Exception as exc:  # no template, parse failure, etc.
        logger.debug("ntc parse failed (%s / %s): %s", platform, command, exc)
        return []


def _discover_via_ssh(device, profile, creds: dict) -> list[dict]:
    from netmiko import ConnectHandler
    from apps.compliance.collector import netmiko_device_type

    host = str(device.management_ip or device.ip_address)
    platform = (device.platform or "").lower()
    device_type = netmiko_device_type(device.vendor, device.platform)
    if device_type == "autodetect":
        device_type = "cisco_ios"
    params = {
        "device_type": device_type,
        "host": host,
        "username": profile.ssh_username,
        "password": creds.get("ssh_password", ""),
        "port": profile.ssh_port or 22,
        "fast_cli": False,
    }

    # FortiOS uses its own "get system interface" syntax — "show interfaces" and
    # the Cisco TextFSM templates do not apply. Dispatch to a dedicated parser.
    if platform == "fortios" or device_type == "fortinet":
        return _discover_via_ssh_fortios(params)

    ntc_platform = LLDP_NTC_PLATFORM.get(device_type, "cisco_ios")

    try:
        conn = ConnectHandler(**params)
    except Exception as exc:
        raise DiscoveryError(f"SSH connection failed: {exc}") from exc
    try:
        # Interfaces: Netmiko's built-in TextFSM (auto-selects the platform template).
        intf = conn.send_command("show interfaces", use_textfsm=True)
        # LLDP: parse the raw text with an explicit ntc-templates platform so the
        # right template is used even when device_type is a generic fallback.
        try:
            lldp_raw = conn.send_command("show lldp neighbors detail")
            lldp = _ntc_parse(ntc_platform, "show lldp neighbors detail", lldp_raw)
        except Exception:
            lldp = []
    finally:
        conn.disconnect()

    if not isinstance(intf, list):
        raise DiscoveryError("could not parse 'show interfaces' output")

    # Map LLDP neighbors by normalised local interface.
    lldp_map: dict[str, dict] = {}
    for n in (lldp or []):
        n = {k.lower(): v for k, v in n.items()}
        local = _norm(n.get("local_interface") or n.get("local_port") or "")
        if not local:
            continue
        host_full = (n.get("neighbor_name") or n.get("neighbor") or n.get("system_name")
                     or n.get("chassis_id") or "")
        lldp_map[local] = {
            "host": host_full,
            "port": (n.get("neighbor_interface") or n.get("neighbor_port_id")
                     or n.get("port_id") or ""),
            "desc": (n.get("neighbor_description") or n.get("neighbor_port_description")
                     or n.get("port_description") or ""),
            "mgmt_ip": (n.get("management_ip") or n.get("mgmt_address")
                        or n.get("mgmt_ip") or n.get("management_address") or ""),
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
        nb = lldp_map.get(_norm(if_name)) or {}
        rows.append({
            "if_index": None,
            "if_name": if_name,
            "if_description": r.get("description", "") or "",
            "if_speed_mbps": _bandwidth_to_mbps(bw),
            "if_type": r.get("hardware_type", "") or "",
            "oper_status": oper,
            "admin_status": admin,
            "lldp_neighbor_hostname": nb.get("host") or None,
            "lldp_neighbor_port": nb.get("port") or None,
            "lldp_neighbor_desc": nb.get("desc") or None,
            "lldp_neighbor_mgmt_ip": nb.get("mgmt_ip") or None,
        })
    return rows


import re as _re

# FortiOS field tokens in "get system interface" output. Each value is captured
# lazily up to the next "key:" token (so multi-word values like the ip+mask pair
# "1.2.3.4 255.255.255.0" stay intact).
_FORTIOS_FIELD = _re.compile(
    r"\b(name|mode|ip|status|type|speed|alias|description):\s*(.*?)(?=\s+[\w-]+:\s|$)"
)
# Interface types that are virtual/internal and not worth monitoring.
_FORTIOS_SKIP_TYPES = {"loopback", "tunnel", "vap-switch", "wl-mesh"}


def _fortios_speed_mbps(speed: str) -> int | None:
    """Parse FortiOS speed strings ('1000Mbps', '10Gbps', '1000full') to Mbps."""
    if not speed:
        return None
    s = str(speed).lower()
    num = "".join(c for c in s if c.isdigit() or c == ".")
    if not num:
        return None
    try:
        val = float(num)
    except ValueError:
        return None
    if "gbps" in s or "gbit" in s:
        val *= 1000
    return int(val) or None


def parse_fortios_interfaces(raw: str) -> list[dict]:
    """
    Parse FortiOS 'get system interface' output into discovery rows. Handles both
    the '== [ portN ]'-delimited block form and the flat one-line-per-interface
    form. Returns the same row shape as the Cisco/TextFSM path.
    """
    if not raw:
        return []
    blocks = _re.split(r"==\s*\[[^\]]*\]", raw)
    candidates = blocks if len(blocks) > 1 else raw.splitlines()
    rows: list[dict] = []
    for body in candidates:
        if "name:" not in body:
            continue
        fields = {k.lower(): v.strip() for k, v in _FORTIOS_FIELD.findall(body)}
        if_name = fields.get("name")
        if not if_name:
            continue
        if_type = (fields.get("type") or "").lower()
        if if_type in _FORTIOS_SKIP_TYPES:
            continue
        status = (fields.get("status") or "").lower()
        oper = "up" if status == "up" else "down"
        rows.append({
            "if_index": None,
            "if_name": if_name,
            "if_description": fields.get("description") or fields.get("alias") or "",
            "if_speed_mbps": _fortios_speed_mbps(fields.get("speed", "")),
            "if_type": if_type,
            "oper_status": oper,
            "admin_status": "up",
            "lldp_neighbor_hostname": None,
            "lldp_neighbor_port": None,
            "lldp_neighbor_desc": None,
            "lldp_neighbor_mgmt_ip": None,
        })
    return rows


def parse_fortios_lldp(raw: str) -> dict[str, dict]:
    """
    Parse FortiOS 'get system lldp neighbors-detail' into {local_iface: neighbor}.
    Output is loosely structured; we pull the System Name / Port ID / Port Descr
    under each 'Interface: <name>' header. Best-effort — returns {} if unparsable.
    """
    if not raw:
        return {}
    out: dict[str, dict] = {}
    local = None
    cur: dict = {}

    def _flush():
        if local and cur:
            out[_norm(local)] = {
                "host": cur.get("system name") or cur.get("chassis id") or "",
                "port": cur.get("port id") or "",
                "desc": cur.get("port description") or "",
                "mgmt_ip": cur.get("management address") or "",
            }

    for line in raw.splitlines():
        m = _re.match(r"\s*Interface:\s*(\S+)", line, _re.IGNORECASE)
        if m:
            _flush()
            local = m.group(1)
            cur = {}
            continue
        m = _re.match(r"\s*([A-Za-z ]+?):\s*(.+)$", line)
        if m and local:
            cur[m.group(1).strip().lower()] = m.group(2).strip()
    _flush()
    return out


def _discover_via_ssh_fortios(params: dict) -> list[dict]:
    """FortiOS interface discovery via 'get system interface' + LLDP (best-effort)."""
    from netmiko import ConnectHandler

    try:
        conn = ConnectHandler(**params)
    except Exception as exc:
        raise DiscoveryError(f"SSH connection failed: {exc}") from exc
    try:
        intf_raw = conn.send_command("get system interface")
        try:
            lldp_raw = conn.send_command("get system lldp neighbors-detail")
            lldp_map = parse_fortios_lldp(lldp_raw)
        except Exception:
            lldp_map = {}
    finally:
        conn.disconnect()

    rows = parse_fortios_interfaces(intf_raw)
    if not rows:
        raise DiscoveryError("could not parse 'get system interface' output")
    for r in rows:
        nb = lldp_map.get(_norm(r["if_name"]))
        if nb:
            r["lldp_neighbor_hostname"] = nb.get("host") or None
            r["lldp_neighbor_port"] = nb.get("port") or None
            r["lldp_neighbor_desc"] = nb.get("desc") or None
            r["lldp_neighbor_mgmt_ip"] = nb.get("mgmt_ip") or None
    return rows


def _norm(name: str) -> str:
    """
    Canonical interface key so abbreviated and full names join.

    LLDP detail reports the local port abbreviated (``Gi1``) while
    ``show interfaces`` uses the full name (``GigabitEthernet1``). Collapse the
    alphabetic prefix to its first two letters + the numeric/slash tail so both
    map to the same key (``gi1``).
    """
    import re

    raw = (name or "").strip().lower().replace(" ", "")
    m = re.match(r"([a-z]+)(.*)", raw)
    if not m:
        return raw
    return m.group(1)[:2] + m.group(2)


def _bandwidth_to_mbps(bw: str) -> int | None:
    """Parse strings like '1000000 Kbit' or '10000000' (Kbit) into Mbps."""
    if not bw:
        return None
    digits = "".join(c for c in str(bw) if c.isdigit())
    if not digits:
        return None
    kbit = int(digits)
    return kbit // 1000 if kbit else None
