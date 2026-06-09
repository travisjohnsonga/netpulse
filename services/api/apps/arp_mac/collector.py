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
import time

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


def _arp_entry_type(value: str) -> str:
    """
    Normalize an ARP entry's static/dynamic flag to ``static``/``dynamic``.
    Only ``static``/``permanent`` map to static; everything else (including an
    empty value or an encapsulation type like Cisco's ``ARPA``) is ``dynamic``.
    """
    v = (value or "").strip().lower()
    if "static" in v or "permanent" in v:
        return "static"
    return "dynamic"


def _normalize_arp(rows: list[dict]) -> list[dict]:
    out = []
    for raw in rows:
        r = _lower_keys(raw)
        ip = _first(r, "ip_address", "address", "ip")
        mac = _first(r, "mac_address", "mac", "hardware_addr")
        if not ip or not mac:
            continue
        # Static/dynamic lives in different columns per vendor (flags/state/
        # entry_type). Cisco's "type" column is the encapsulation (ARPA), not a
        # static/dynamic flag, so it's deliberately excluded.
        out.append({
            "ip_address": ip,
            "mac_address": normalize_mac(mac),
            "interface": _first(r, "interface", "intf", "port"),
            "vlan": _to_int(_first(r, "vlan")),
            "age_minutes": _to_int(_first(r, "age", "age_min", "age_minutes")),
            "protocol": _first(r, "protocol") or "Internet",
            "entry_type": _arp_entry_type(_first(r, "entry_type", "flags", "state")),
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
            "entry_type": "dynamic",   # FortiOS ARP carries no static/dynamic flag
        })
    return out


# SonicWall "show arp caches" TIMEOUT column: "Expires in 10 minutes" → 10,
# "Permanent published" → None.
_SONICWALL_AGE_RE = re.compile(r"(\d+)\s+minutes?")


def _parse_sonicwall_arp(output: str) -> list[dict]:
    """
    Parse SonicWall ``show arp caches`` via the bundled TextFSM template into
    normalized ARP rows. The device-reported VENDOR column is dropped — the API
    derives the vendor from the MAC OUI at read time — but the Static/Dynamic
    TYPE is mapped to ``entry_type``.
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
            "entry_type": _arp_entry_type(r.get("type", "")),   # Static/Dynamic
        })
    return out


def _drive_sonicwall_shell(shell, command: str, password: str = "", *,
                           banner_wait: float = 2.0, drain_wait: float = 2.0,
                           cmd_wait: float = 5.0, settle: float = 0.5,
                           max_idle: int = 3) -> str:
    """
    Drive an interactive SonicOS shell and return the raw output of ``command``.

    SonicOS prints a login banner that re-prompts for the password (see below)
    and pages long output by default. paramiko's interactive shell lets us
    complete the shell-side password handshake, DISABLE paging with a separate
    ``no cli pager session`` command (draining its response), then issue the
    command and read the full reply — long ARP tables come back complete with no
    ``--More--`` truncation. The generic Netmiko driver mis-times all of this.

    Double password: SonicWall asks for the password AGAIN on the interactive
    shell even though paramiko already authenticated the SSH session (the banner
    ends with "Access denied\nPassword:"). When that prompt is seen we re-send
    the same password before continuing. This is normal SonicOS behavior; both
    prompts take the same password.
    """
    # Read the login banner / initial prompt left in the channel after auth.
    time.sleep(banner_wait)
    banner = ""
    if shell.recv_ready():
        banner = shell.recv(65535).decode("utf-8", errors="ignore")

    # SonicWall re-prompts for the password on the interactive shell.
    if "Password:" in banner or "Access denied" in banner:
        shell.send(password + "\n")
        time.sleep(banner_wait)
        if shell.recv_ready():
            shell.recv(65535)  # drain the post-login prompt

    # Disable CLI paging as its own command so long tables aren't truncated by
    # --More--, then drain its response before issuing the real command.
    shell.send("no cli pager session\n")
    time.sleep(drain_wait)
    if shell.recv_ready():
        shell.recv(65535)  # drain the pager-disable response

    # Paging is now off — issue the command and read the full reply.
    shell.send(command + "\n")
    time.sleep(cmd_wait)

    output = ""
    idle = 0
    while True:
        if shell.recv_ready():
            output += shell.recv(65535).decode("utf-8", errors="ignore")
            idle = 0
            time.sleep(settle)
        else:
            idle += 1
            if idle >= max_idle:
                break
            time.sleep(settle)
    return output


def _collect_sonicwall_arp(host: str, username: str, password: str, port: int) -> list[dict]:
    """
    Read the ARP cache from a SonicWall over a direct paramiko SSH shell.

    SonicOS has no Netmiko driver and its login banner interrupts Netmiko's
    generic-driver authentication, so we connect with paramiko (generous
    banner/auth timeouts) and drive the interactive shell ourselves
    (``_drive_sonicwall_shell`` handles the banner + ``--More--`` paging).
    """
    import paramiko  # lazy import (paramiko ships with netmiko)

    ssh = paramiko.SSHClient()
    # NOTE: AutoAddPolicy is intentional for network-device monitoring, where
    # device host keys are not pre-distributed and devices may be re-imaged.
    # Risk accepted: collection runs only against operator-configured devices on
    # the internal management network, never arbitrary/internet hosts.
    # TODO: implement per-device host-key pinning once a key store exists.
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())  # nosec B507 - see note above
    try:
        ssh.connect(
            hostname=host,
            port=port,
            username=username,
            password=password,
            timeout=30,
            banner_timeout=30,   # wait for the SonicOS login banner
            auth_timeout=30,     # wait for the "Password:" prompt after the banner
            look_for_keys=False,
            allow_agent=False,
        )
        shell = ssh.invoke_shell()
        # Pass the password through — SonicWall re-prompts for it on the shell.
        out = _drive_sonicwall_shell(shell, "show arp caches", password)
    finally:
        try:
            ssh.close()
        except Exception:
            pass
    return _parse_sonicwall_arp(out)


def _collect_aos_cx_rest(host: str, username: str, password: str):
    """
    Collect ARP + MAC over the AOS-CX REST API.

    Returns ``(arp, mac)`` on success (MAC addresses normalised to the common
    form), or ``None`` when REST is unusable so the caller falls back to SSH.
    Reuses the device's SSH credentials — the same local account works for REST
    on AOS-CX. An empty-but-successful read returns ``([], [])`` (not ``None``)
    so we don't double-collect over SSH for a device that genuinely has no
    entries.
    """
    if not (username and password):
        return None
    try:
        from apps.devices.aos_cx_client import AOSCXClient
        with AOSCXClient(host) as client:
            client.login(username, password)
            arp = client.get_arp_table()
            mac = client.get_mac_table()
    except Exception as exc:  # noqa: BLE001 — any REST failure → SSH fallback
        logger.warning("arp_mac: AOS-CX REST to %s failed: %s", host, exc)
        return None
    for e in arp:
        e["mac_address"] = normalize_mac(e.get("mac_address", ""))
    for e in mac:
        e["mac_address"] = normalize_mac(e.get("mac_address", ""))
    return arp, mac


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

    host = device_host(device)
    password = secrets.get("ssh_password", "")
    port = getattr(device.credential_profile, "ssh_port", 22) or 22
    arp_entries: list[dict] = []
    mac_entries: list[dict] = []

    if platform == "sonicwall":
        # SonicOS has no Netmiko driver and its login banner interrupts the
        # generic driver's auth — drive a direct paramiko shell instead. ARP
        # only (firewalls have no MAC address-table).
        try:
            arp_entries = _collect_sonicwall_arp(host, username, password, port)
        except Exception as exc:
            logger.error("arp_mac: SonicWall SSH to %s failed: %s", device.hostname, exc)
            return [], []
        logger.info("arp_mac: %s — %d ARP, %d MAC", device.hostname, len(arp_entries), 0)
        return arp_entries, []

    if platform == "aos_cx":
        # AOS-CX exposes ARP + the MAC table over the REST API (structured JSON,
        # a single authenticated session — more reliable than SSH/TextFSM). Try
        # REST first; fall through to the Netmiko/SSH path on any failure.
        rest = _collect_aos_cx_rest(host, username, password)
        if rest is not None:
            arp_entries, mac_entries = rest
            logger.info("arp_mac: %s — %d ARP, %d MAC (REST)",
                        device.hostname, len(arp_entries), len(mac_entries))
            return arp_entries, mac_entries
        logger.info("arp_mac: %s — AOS-CX REST unavailable, falling back to SSH", device.hostname)

    from netmiko import ConnectHandler  # lazy import

    params = {
        "device_type": device_type,
        "host": host,
        "username": username,
        "password": password,
        "port": port,
        "fast_cli": False,
        "conn_timeout": 30,
    }
    try:
        conn = ConnectHandler(**params)
    except Exception as exc:
        logger.error("arp_mac: SSH connect to %s failed: %s", device.hostname, exc)
        return [], []
    try:
        arp_cmd = ARP_COMMANDS.get(platform)
        if arp_cmd:
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
                    "entry_type": e.get("entry_type") or "dynamic",
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
