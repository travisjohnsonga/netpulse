"""
ARP + MAC address-table collection over SSH (Netmiko + ntc-templates).

Reuses the compliance collector's Netmiko device-type resolution, host
resolution and OpenBao credential fetch. Parsing is done by ntc-templates
(``use_textfsm=True``) which already ships templates for cisco_ios/xe/nxos,
arista_eos, juniper_junos and aruba_aoscx; field names vary across vendors so
the results are normalized to a common schema here. FortiOS has no MAC table
(ARP only) and its ``get system arp`` is parsed with a small regex fallback.
"""
from __future__ import annotations

import logging
import os
import re

from apps.compliance.collector import device_host, netmiko_device_type
from .normalize import normalize_mac

logger = logging.getLogger(__name__)

# Custom TextFSM templates (fallback when ntc-templates doesn't parse a vendor
# form — e.g. AOS-CX bare "show arp"). Lives in the collectors app.
_TEMPLATE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "collectors", "templates")

# Platforms we can collect from, and how to reach them with Netmiko.
DEVICE_TYPE_MAP = {
    "ios": "cisco_ios",
    "ios_xe": "cisco_xe",
    "ios_xr": "cisco_xr",
    "nxos": "cisco_nxos",
    "eos": "arista_eos",
    "junos": "juniper_junos",
    "aos_cx": "aruba_aoscx",
    "aruba": "aruba_os",
    "fortios": "fortinet",
    # SonicOS has no Netmiko driver — drive the CLI with the generic handler.
    "sonicwall": "generic",
}

# ARP command per platform — chosen to match an available ntc-template.
ARP_COMMANDS = {
    "ios": "show ip arp",
    "ios_xe": "show ip arp",
    "ios_xr": "show arp",
    "nxos": "show ip arp",
    "eos": "show ip arp",
    "junos": "show arp no-resolve",
    "aos_cx": "show arp all-vrfs",
    "aruba": "show arp",
    "fortios": "get system arp",
    "sonicwall": "show arp caches",
}

# MAC address-table command per platform. FortiOS and SonicWall (firewalls, not
# switches) have no traditional MAC table — they are absent here on purpose.
MAC_COMMANDS = {
    "ios": "show mac address-table",
    "ios_xe": "show mac address-table",
    "ios_xr": "show l2vpn forwarding bridge-domain mac-address location 0/0/CPU0",
    "nxos": "show mac address-table",
    "eos": "show mac address-table",
    "junos": "show ethernet-switching table",
    "aos_cx": "show mac-address-table",
    "aruba": "show mac-address",
}

_INT_RE = re.compile(r"-?\d+")
# FortiOS "get system arp": "10.150.0.1   0   aa:bb:cc:dd:ee:ff   port1"
_FORTIOS_ARP_RE = re.compile(
    r"^\s*(\d{1,3}(?:\.\d{1,3}){3})\s+(\d+|-)\s+([0-9a-fA-F:]{17})\s+(\S+)")


def _resolve_device_type(device) -> str:
    dt = DEVICE_TYPE_MAP.get((device.platform or "").lower())
    return dt or netmiko_device_type(getattr(device, "vendor", ""), device.platform)


def _to_int(v):
    if v in (None, "", "-"):
        return None
    m = _INT_RE.search(str(v))
    return int(m.group()) if m else None


def _lower_keys(row: dict) -> dict:
    return {str(k).lower(): v for k, v in row.items()}


def _first(row: dict, *keys):
    for k in keys:
        if row.get(k):
            return row[k]
    return ""


def _normalize_arp(rows: list[dict]) -> list[dict]:
    out = []
    for raw in rows:
        r = _lower_keys(raw)
        ip = _first(r, "ip_address", "address", "ip")
        mac = _first(r, "mac_address", "mac", "hardware_addr")
        if not ip or not mac:
            continue
        out.append({
            "ip_address": ip,
            "mac_address": normalize_mac(mac),
            "interface": _first(r, "interface", "intf", "port"),
            "vlan": _to_int(_first(r, "vlan")),
            "age_minutes": _to_int(_first(r, "age", "age_min", "age_minutes")),
            "protocol": _first(r, "protocol") or "Internet",
        })
    return out


def _normalize_mac(rows: list[dict]) -> list[dict]:
    out = []
    for raw in rows:
        r = _lower_keys(raw)
        mac = _first(r, "destination_address", "mac_address", "mac")
        if not mac:
            continue
        out.append({
            "mac_address": normalize_mac(mac),
            "vlan": _to_int(_first(r, "vlan")),
            "interface": _first(r, "destination_port", "ports", "interface", "port"),
            "entry_type": (_first(r, "type", "entry_type") or "dynamic").lower(),
        })
    return out


def _parse_fortios_arp(output: str) -> list[dict]:
    out = []
    for line in output.splitlines():
        m = _FORTIOS_ARP_RE.match(line)
        if not m:
            continue
        ip, age, mac, intf = m.groups()
        out.append({
            "ip_address": ip, "mac_address": normalize_mac(mac),
            "interface": intf, "vlan": None,
            "age_minutes": _to_int(age), "protocol": "Internet",
        })
    return out


# SonicWall "show arp caches" TIMEOUT column: "Expires in 10 minutes" → 10,
# "Permanent published" → None.
_SONICWALL_AGE_RE = re.compile(r"(\d+)\s+minutes?")


def _parse_sonicwall_arp(output: str) -> list[dict]:
    """
    Parse SonicWall ``show arp caches`` via the bundled TextFSM template into
    normalized ARP rows. The device-reported VENDOR column is dropped — the API
    derives the vendor from the MAC OUI at read time — and the Static/Dynamic
    TYPE has no column on ARPEntry, so it is not persisted.
    """
    out = []
    for raw in _textfsm_parse(output, "sonicwall_show_arp_caches.textfsm"):
        r = _lower_keys(raw)
        ip = r.get("ip_address")
        mac = r.get("mac_address")
        if not ip or not mac:
            continue
        m = _SONICWALL_AGE_RE.search(r.get("timeout", "") or "")
        out.append({
            "ip_address": ip,
            "mac_address": normalize_mac(mac),
            "interface": r.get("interface", ""),   # SonicWall "X0:V500" form
            "vlan": None,
            "age_minutes": int(m.group(1)) if m else None,
            "protocol": "Internet",
        })
    return out


def _collect_sonicwall_arp(conn) -> list[dict]:
    """
    Read the ARP cache from a SonicWall over the generic Netmiko handler. SonicOS
    has no driver, so disable CLI paging first (avoids ``--More--`` truncation)
    and fall back to timing-based reads when the prompt isn't auto-detected.
    """
    try:
        conn.send_command_timing("no cli pager session", strip_prompt=False, strip_command=False)
    except Exception:  # not all SonicOS versions support it — best effort
        pass
    try:
        out = conn.send_command("show arp caches", expect_string=r"[>#]\s*$", read_timeout=60)
    except Exception:
        out = conn.send_command_timing("show arp caches", strip_prompt=True, strip_command=True)
    return _parse_sonicwall_arp(out)


def _textfsm_parse(output: str, template_name: str) -> list[dict]:
    """Parse raw output with a bundled custom TextFSM template → list of dicts."""
    path = os.path.join(_TEMPLATE_DIR, template_name)
    if not output or not os.path.exists(path):
        return []
    try:
        import textfsm
        with open(path) as fh:
            fsm = textfsm.TextFSM(fh)
        return [dict(zip(fsm.header, row)) for row in fsm.ParseText(output)]
    except Exception as exc:
        logger.warning("arp_mac: TextFSM parse with %s failed: %s", template_name, exc)
        return []


def _send(conn, command: str):
    """send_command with ntc-templates parsing; returns a list (parsed) or str."""
    try:
        return conn.send_command(command, use_textfsm=True, read_timeout=60)
    except Exception as exc:  # command unsupported on this device, etc.
        logger.warning("arp_mac: '%s' failed: %s", command, exc)
        return []


def collect_arp_mac(device, secrets: dict, username: str) -> tuple[list[dict], list[dict]]:
    """
    Collect and normalize the device's ARP and MAC tables. Returns
    ``(arp_entries, mac_entries)`` as lists of plain dicts (empty on failure).
    Pure I/O + parsing — the caller persists the rows.
    """
    platform = (device.platform or "").lower()
    device_type = _resolve_device_type(device)
    if not device_type or device_type == "autodetect":
        logger.warning("arp_mac: no Netmiko device_type for %s (%s)", device.hostname, platform)
        return [], []

    from netmiko import ConnectHandler  # lazy import

    params = {
        "device_type": device_type,
        "host": device_host(device),
        "username": username,
        "password": secrets.get("ssh_password", ""),
        "port": getattr(device.credential_profile, "ssh_port", 22) or 22,
        "fast_cli": False,
        "conn_timeout": 30,
    }
    if platform == "sonicwall":
        params["global_delay_factor"] = 2  # SonicOS CLI is slow over the generic driver
    arp_entries: list[dict] = []
    mac_entries: list[dict] = []
    try:
        conn = ConnectHandler(**params)
    except Exception as exc:
        logger.error("arp_mac: SSH connect to %s failed: %s", device.hostname, exc)
        return [], []
    try:
        arp_cmd = ARP_COMMANDS.get(platform)
        if platform == "sonicwall":
            # Custom CLI path (paging + generic driver + custom TextFSM template).
            arp_entries = _collect_sonicwall_arp(conn)
        elif arp_cmd:
            out = _send(conn, arp_cmd)
            if isinstance(out, list):
                arp_entries = _normalize_arp(out)
            elif platform == "fortios":
                arp_entries = _parse_fortios_arp(out)
            elif platform == "aos_cx":
                arp_entries = _normalize_arp(_textfsm_parse(out, "aruba_aoscx_show_arp.textfsm"))

        mac_cmd = MAC_COMMANDS.get(platform)
        if mac_cmd:
            out = _send(conn, mac_cmd)
            if isinstance(out, list):
                mac_entries = _normalize_mac(out)
            elif platform == "aos_cx":
                mac_entries = _normalize_mac(_textfsm_parse(out, "aruba_aoscx_show_mac_address_table.textfsm"))
    finally:
        try:
            conn.disconnect()
        except Exception:
            pass

    logger.info("arp_mac: %s — %d ARP, %d MAC", device.hostname, len(arp_entries), len(mac_entries))
    return arp_entries, mac_entries


def store_arp_mac(device, arp: list[dict], mac: list[dict]) -> tuple[int, int]:
    """
    Persist collected rows. ARP is upserted by (device, ip) so the table tracks
    current bindings; the MAC table is replaced wholesale (entries age out and
    move ports). Returns (arp_count, mac_count). Runs in a transaction.
    """
    from django.db import transaction
    from .models import ARPEntry, MACEntry

    with transaction.atomic():
        seen_ips = set()
        for e in arp:
            if not e.get("ip_address") or e["ip_address"] in seen_ips:
                continue
            seen_ips.add(e["ip_address"])
            ARPEntry.objects.update_or_create(
                device=device, ip_address=e["ip_address"],
                defaults={
                    "mac_address": e.get("mac_address", ""),
                    "interface": e.get("interface", ""),
                    "vlan": e.get("vlan"),
                    "age_minutes": e.get("age_minutes"),
                    "protocol": e.get("protocol") or "Internet",
                },
            )
        # Drop ARP rows no longer present (released bindings).
        if seen_ips:
            ARPEntry.objects.filter(device=device).exclude(ip_address__in=seen_ips).delete()

        MACEntry.objects.filter(device=device).delete()
        rows, seen = [], set()
        for e in mac:
            key = (e.get("mac_address"), e.get("vlan"))
            if not e.get("mac_address") or key in seen:
                continue
            seen.add(key)
            rows.append(MACEntry(
                device=device, mac_address=e["mac_address"], vlan=e.get("vlan"),
                interface=e.get("interface", ""), entry_type=e.get("entry_type") or "dynamic",
            ))
        MACEntry.objects.bulk_create(rows)
    return len(seen_ips), len(rows)
