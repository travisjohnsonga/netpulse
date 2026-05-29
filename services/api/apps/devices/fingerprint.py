"""
Best-effort device fingerprinting for the Add-Device "auto-detect" flow.

Given just an IP, probe the common management ports and try to infer the vendor
from the SSH banner. Full platform/OS/model detection needs SNMP sysDescr or an
authenticated session — that runs in the poller/ingest layer, which holds the
protocol stacks and credentials. This module returns what's cheaply knowable
from an unauthenticated probe and is honest about the rest.
"""
from __future__ import annotations

import socket

# Management ports worth probing, with a friendly protocol label.
PROBE_PORTS = [
    (22, "ssh"),
    (443, "https"),
    (830, "netconf"),
    (80, "http"),
    (57400, "gnmi"),
    (161, "snmp"),  # UDP — datagram send only, see below
]

# SSH banner vendor hints.
_VENDOR_HINTS = [
    ("cisco", "Cisco"),
    ("arista", "Arista"),
    ("juniper", "Juniper"),
    ("junos", "Juniper"),
    ("nokia", "Nokia"),
    ("huawei", "Huawei"),
    ("fortinet", "Fortinet"),
    ("paloalto", "Palo Alto"),
    ("mikrotik", "MikroTik"),
]


def _tcp_open(ip: str, port: int, timeout: float) -> bool:
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except OSError:
        return False


def _ssh_banner(ip: str, timeout: float) -> str:
    try:
        with socket.create_connection((ip, 22), timeout=timeout) as s:
            s.settimeout(timeout)
            return s.recv(256).decode(errors="ignore").strip()
    except OSError:
        return ""


def fingerprint(ip: str, timeout: float = 2.0) -> dict:
    """
    Return a best-effort fingerprint. Never raises.

    Shape: {reachable, open_ports, banner, vendor, platform, os_version,
            model, detail}
    """
    open_ports: list[int] = []
    for port, _label in PROBE_PORTS:
        if port == 161:
            continue  # UDP — skip in the TCP sweep
        if _tcp_open(ip, port, timeout):
            open_ports.append(port)

    reachable = bool(open_ports)
    banner = _ssh_banner(ip, timeout) if 22 in open_ports else ""

    vendor = None
    lowered = banner.lower()
    for needle, name in _VENDOR_HINTS:
        if needle in lowered:
            vendor = name
            break

    if not reachable:
        detail = (
            f"No management ports reachable on {ip}. Confirm the IP and that the "
            "device is online, or select the platform manually."
        )
    elif vendor:
        detail = f"Detected {vendor} from SSH banner. Confirm platform/version below."
    else:
        detail = (
            f"{ip} is reachable on {open_ports} but the vendor couldn't be inferred "
            "without SNMP/credentials. Select the platform manually."
        )

    return {
        "reachable": reachable,
        "open_ports": open_ports,
        "banner": banner,
        "vendor": vendor,
        # These need authenticated/SNMP probing — left null for manual entry.
        "platform": None,
        "os_version": None,
        "model": None,
        "detail": detail,
    }
