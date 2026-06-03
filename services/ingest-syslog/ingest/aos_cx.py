"""
HPE AOS-CX syslog normalization.

AOS-CX emits a pipe-delimited event format after the syslog header::

    hpe-restd: Event|4657|LOG_INFO|AMM|-|User admin logged out of REST session...
    hpe-config: Event|6801|LOG_INFO|AMM|-|Copying configs from running-config...
    tpmtd: Event|13601|LOG_INFO|||TPM_Sign requested...

Fields: ``{process}: Event|{id}|{level}|{module}|{submodule}|{message}``. These
functions detect that shape, parse the fields, synthesise a clean human-readable
message + syslog severity + program label, and tag Aruba Central-originated
events. All functions are pure; the parser applies them after RFC 3164/5424
parsing (mirrors ingest/fortios.py).
"""
from __future__ import annotations

import re
from typing import Any

# Optional leading "process:" (RFC 3164 may already split it into app_name),
# then Event|id|level|module|submodule|message. module/submodule may be empty.
_AOS_CX_RE = re.compile(
    r"^(?:(?P<process>\S+?):\s+)?"
    r"Event\|(?P<event_id>\d+)\|"
    r"(?P<level>LOG_[A-Z]+)\|"
    r"(?P<module>[^|]*)\|"
    r"(?P<submodule>[^|]*)\|"
    r"(?P<message>.*)$",
    re.DOTALL,
)

# AOS-CX LOG_* level → numeric syslog severity (RFC 5424 scale).
_LEVEL_TO_SEVERITY: dict[str, int] = {
    "LOG_EMERG": 0, "LOG_EMER": 0, "LOG_ALERT": 1,
    "LOG_CRIT": 2, "LOG_ERR": 3, "LOG_ERROR": 3,
    "LOG_WARN": 4, "LOG_WARNING": 4, "LOG_NOTICE": 5,
    "LOG_INFO": 6, "LOG_DEBUG": 7,
}

# Aruba Central control-plane hostname seen in Central-managed events.
_CENTRAL_HINT = "central.arubanetworks.com"


def is_aos_cx_log(message: str) -> bool:
    """True if the message looks like an AOS-CX ``Event|id|LOG_…|`` record."""
    if not message:
        return False
    return bool(re.search(r"Event\|\d+\|LOG_[A-Z]+\|", message))


def parse_aos_cx_log(message: str) -> dict[str, str] | None:
    """Parse the AOS-CX event format into its fields, or None if it doesn't match."""
    m = _AOS_CX_RE.match(message or "")
    if not m:
        return None
    d = m.groupdict()
    return {
        "process": (d.get("process") or "").strip(),
        "event_id": d.get("event_id") or "",
        "level": d.get("level") or "",
        "module": (d.get("module") or "").strip(),
        "submodule": (d.get("submodule") or "").strip(),
        "message": (d.get("message") or "").strip(),
    }


def format_aos_cx_message(f: dict[str, str]) -> str:
    """Compact human-readable message: "[process/module] text" (skip empties)."""
    label = "/".join(p for p in (f.get("process"), f.get("module")) if p and p != "-")
    text = f.get("message") or ""
    return f"[{label}] {text}".strip() if label else text


def map_aos_cx_severity(f: dict[str, str]) -> int | None:
    """AOS-CX LOG_* level → numeric syslog severity, or None if unrecognised."""
    return _LEVEL_TO_SEVERITY.get((f.get("level") or "").upper())


def aos_cx_extras(f: dict[str, str]) -> dict[str, str]:
    """Selected AOS-CX fields surfaced into extras for search/filtering."""
    out: dict[str, str] = {}
    if f.get("process"):
        out["aos_cx_process"] = f["process"]
    if f.get("event_id"):
        out["aos_cx_event_id"] = f["event_id"]
    if f.get("module") and f["module"] != "-":
        out["aos_cx_module"] = f["module"]
    return out


def normalize(result: dict[str, Any], severities: dict[int, str]) -> None:
    """
    Mutate a parsed syslog `result` in place to normalise an AOS-CX record.
    `result["message"]` must hold the ``…Event|…`` text; `raw` is preserved.
    """
    fields = parse_aos_cx_log(result["message"])
    if not fields:
        return

    # Prefer the in-band process name; fall back to the RFC 3164 app_name.
    if not fields["process"] and result.get("app_name"):
        fields["process"] = result["app_name"]

    raw_text = fields["message"]
    result["message"] = format_aos_cx_message(fields)

    sev = map_aos_cx_severity(fields)
    if sev is not None:
        result["severity"] = sev
        result["severity_name"] = severities.get(sev, str(sev))

    program = (fields["process"] or "AOS-CX").upper()
    result["program"] = program
    result["app_name"] = fields["process"] or result.get("app_name")
    result["vendor"] = "aruba"

    extras = result.get("extras") or {}
    extras.update(aos_cx_extras(fields))

    # Tag Aruba Central-originated events so Central-managed config changes /
    # auth sessions are distinguishable from manual admin actions.
    if _CENTRAL_HINT in raw_text.lower():
        extras["aos_cx_source"] = "aruba_central"
        extras["aruba_central"] = "true"

    result["extras"] = extras
