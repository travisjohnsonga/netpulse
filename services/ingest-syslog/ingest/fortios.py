"""
FortiOS (FortiGate) syslog normalization.

FortiOS does not use the Cisco IOS ``%FAC-SEV-MNEMONIC:`` mnemonic format. It
emits structured key=value records, e.g.::

    date=2026-05-31 time=11:41:41 devname="fw1" devid="FGT..." type="traffic"
    subtype="forward" level="notice" action="client-rst" service="HTTPS"
    srcip=192.168.98.153 srcport=10996 dstip=154.52.23.136 dstport=443

These functions detect that shape, parse the fields, and synthesise a clean
human-readable message + a syslog severity + a program label, so FortiOS logs
read like every other device's in the UI. All functions are pure; the parser
applies them after RFC 3164/5424 parsing.
"""
from __future__ import annotations

import re
from typing import Any

# key=value or key="quoted value" (quoted values may contain spaces).
_KV_RE = re.compile(r'(\w+)=(?:"([^"]*)"|(\S+))')

# FortiOS level → numeric syslog severity (RFC 5424 scale).
_LEVEL_TO_SEVERITY: dict[str, int] = {
    "emergency": 0, "alert": 1, "critical": 2, "error": 3,
    "warning": 4, "notice": 5, "information": 6, "informational": 6,
    "info": 6, "debug": 7,
}

# Fields surfaced into the document's extras for search/filtering.
_EXTRA_FIELDS: dict[str, str] = {
    "type": "fortios_type", "subtype": "fortios_subtype",
    "action": "fortios_action", "service": "fortios_service",
    "srcip": "fortios_srcip", "dstip": "fortios_dstip",
    "user": "fortios_user", "policyid": "fortios_policyid",
}


def is_fortios_log(message: str) -> bool:
    """True if the message looks like a FortiOS key=value record."""
    if not message:
        return False
    return "devname=" in message and "type=" in message and "level=" in message


def parse_fortios_log(message: str) -> dict[str, str]:
    """Parse all key=value / key="value" pairs into a flat dict."""
    out: dict[str, str] = {}
    for m in _KV_RE.finditer(message or ""):
        out[m.group(1)] = m.group(2) if m.group(2) is not None else m.group(3)
    return out


def _endpoint(ip: str, port: str) -> str:
    return f"{ip}:{port}" if (ip and port) else (ip or "")


def format_fortios_message(f: dict[str, str]) -> str:
    """Build a concise human-readable message from parsed FortiOS fields."""
    typ = (f.get("type") or "").lower()
    sub = (f.get("subtype") or "").lower()
    msg = f.get("msg") or f.get("logdesc") or ""

    if typ == "traffic":
        action = (f.get("action") or "").upper()
        service = f.get("service") or f.get("proto") or ""
        src = _endpoint(f.get("srcip", ""), f.get("srcport", ""))
        dst = _endpoint(f.get("dstip", ""), f.get("dstport", ""))
        head = " ".join(p for p in (action, service) if p)
        flow = f"{src} → {dst}" if (src or dst) else ""
        out = " ".join(p for p in (head, flow) if p).strip()
        return out or msg or "traffic"

    if typ == "utm":
        return (f"UTM {sub}: {msg}".strip() if sub else f"UTM: {msg}".strip()).rstrip(":").strip()

    if typ == "anomaly":
        return f"ANOMALY {msg}".strip()

    # event / system / security-rating and anything else: prefer logdesc, then msg.
    return f.get("logdesc") or msg or f.get("action") or typ or "event"


def map_fortios_severity(f: dict[str, str]) -> int | None:
    """FortiOS level → numeric syslog severity, or None if unrecognised."""
    return _LEVEL_TO_SEVERITY.get((f.get("level") or "").lower())


def map_fortios_program(f: dict[str, str]) -> str:
    """FortiOS type/subtype → a program label (TRAFFIC/SYSTEM/SECURITY/UTM/…)."""
    typ = (f.get("type") or "").lower()
    sub = (f.get("subtype") or "").lower()
    if typ == "traffic":
        return "TRAFFIC"
    if typ == "event":
        if sub == "system":
            return "SYSTEM"
        if sub == "security-rating":
            return "SECURITY"
        return "EVENT"
    if typ == "utm":
        return "UTM"
    if typ == "anomaly":
        return "ANOMALY"
    return (typ or "fortios").upper()


def fortios_extras(f: dict[str, str]) -> dict[str, str]:
    """Selected FortiOS fields to carry into the document's extras dict."""
    return {dest: f[src] for src, dest in _EXTRA_FIELDS.items() if f.get(src)}


def normalize(result: dict[str, Any], severities: dict[int, str]) -> None:
    """
    Mutate a parsed syslog `result` in place to normalise a FortiOS record.
    `result["message"]` must already hold the FortiOS key=value text; `raw` is
    preserved. `severities` is the parser's numeric→name severity table.
    """
    fields = parse_fortios_log(result["message"])
    if not fields:
        return
    result["message"] = format_fortios_message(fields)
    sev = map_fortios_severity(fields)
    if sev is not None:
        result["severity"] = sev
        result["severity_name"] = severities.get(sev, str(sev))
    program = map_fortios_program(fields)
    result["program"] = program
    result["app_name"] = program
    result["vendor"] = "fortinet"
    extras = result.get("extras") or {}
    extras.update(fortios_extras(fields))
    result["extras"] = extras
