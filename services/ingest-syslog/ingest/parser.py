"""
RFC 3164 and RFC 5424 syslog parser.

Auto-detects format from the priority header:
  - <PRI> followed by a digit  → RFC 5424 (version number present)
  - <PRI> followed by a letter → RFC 3164 (timestamp starts with month name)

Output is always a flat dict ready for JSON serialisation and NATS publishing.
"""
import re
from datetime import datetime, timezone
from typing import Any

from . import aos_cx, fortios

# ── Lookup tables ─────────────────────────────────────────────────────────────

FACILITIES: dict[int, str] = {
    0: "kern",      1: "user",      2: "mail",       3: "daemon",
    4: "auth",      5: "syslog",    6: "lpr",        7: "news",
    8: "uucp",      9: "cron",      10: "authpriv",  11: "ftp",
    12: "ntp",      13: "security", 14: "console",   15: "solaris-cron",
    16: "local0",   17: "local1",   18: "local2",    19: "local3",
    20: "local4",   21: "local5",   22: "local6",    23: "local7",
}

SEVERITIES: dict[int, str] = {
    0: "emerg", 1: "alert", 2: "crit",    3: "err",
    4: "warning", 5: "notice", 6: "info", 7: "debug",
}

# ── Compiled regexes ──────────────────────────────────────────────────────────

# Matches the <PRI> header common to both formats.
_PRI_RE = re.compile(r"^<(\d{1,3})>")

# RFC 5424 fields after <PRI>: VERSION SP TIMESTAMP SP HOSTNAME SP APP-NAME SP PROCID SP MSGID SP
_RFC5424_HEADER_RE = re.compile(
    r"^(\d+)"                    # version
    r"\s+(\S+)"                  # timestamp
    r"\s+(\S+)"                  # hostname
    r"\s+(\S+)"                  # app-name
    r"\s+(\S+)"                  # procid
    r"\s+(\S+)"                  # msgid
    r"\s+"                       # space before SD / MSG
)

# RFC 3164 fields after <PRI>: TIMESTAMP HOSTNAME [TAG[PID]:] MSG
_RFC3164_HEADER_RE = re.compile(
    r"^(\w{3}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2})"   # timestamp (Mmm DD HH:MM:SS)
    r"\s+(\S+)"                                    # hostname
    r"(?:\s+([^[:\s]+)(?:\[(\d+)\])?:)?"          # optional: TAG[PID]:
    r"\s*(.*)$",                                   # message
    re.DOTALL,
)

# Cisco IOS/IOS-XE/NX-OS log mnemonic, e.g. "%BGP-5-ADJCHANGE:" — the real
# start of the message. Network gear prefixes this with a sequence number and a
# device-local timestamp that add noise; anchoring here drops the prefix.
_IOS_MNEMONIC = re.compile(r"%[A-Z0-9_]+-\d-[A-Z0-9_]+:")
_RE_SEQ = re.compile(r"^\d+:\s*")
_RE_PRI = re.compile(r"^<\d+>")
_RE_SD_TAG = re.compile(r"^\[[^\]]*\]:\s*")
# A leading device-local timestamp like "*May 30 2026 12:00:00.123 UTC:".
_RE_TS_PREFIX = re.compile(r"^\*?[A-Z][a-z]{2}\s+\d+\s+[\d:.]+(\s+\S+)?:\s*")


def clean_syslog_message(msg: str, hostname: str | None = None) -> str:
    """
    Strip transport/device noise from a syslog message, leaving the human text.

    Anchors on the Cisco IOS mnemonic (``%FAC-SEV-MNEMONIC:``) when present —
    everything before it (sequence number, device timestamp) is noise. Otherwise
    peels common leading prefixes in order: residual <PRI>, a sequence number,
    the device's own ``hostname:`` echo, an ``[origin]:`` tag, and a leading
    device-local timestamp. Idempotent and safe on already-clean messages.
    """
    if not msg:
        return msg
    m = _IOS_MNEMONIC.search(msg)
    if m:
        return msg[m.start():].strip()
    s = _RE_PRI.sub("", msg)
    s = _RE_SEQ.sub("", s)
    if hostname:
        s = re.sub(rf"^{re.escape(hostname)}:\s*", "", s)
    s = _RE_SD_TAG.sub("", s)
    s = _RE_TS_PREFIX.sub("", s)
    return s.strip()


# ── Public API ────────────────────────────────────────────────────────────────

def parse(
    data: bytes,
    source_ip: str,
    source_port: int,
    transport: str,
) -> dict[str, Any]:
    """
    Parse a single syslog message received from source_ip:source_port via transport.

    Returns a dict with all fields normalised.  Never raises; falls back to a
    raw-message envelope on any parse failure.
    """
    # Strip UTF-8 BOM, trailing NUL / whitespace
    raw = data.decode("utf-8", errors="replace").lstrip("\xef\xbb\xbf").rstrip("\x00\r\n ")

    m = _PRI_RE.match(raw)
    if not m:
        return _raw_envelope(raw, source_ip, source_port, transport)

    priority = int(m.group(1))
    rest = raw[m.end():]

    if rest and rest[0].isdigit():
        result = _parse_rfc5424(raw, priority, rest, source_ip, source_port, transport)
    else:
        result = _parse_rfc3164(raw, priority, rest, source_ip, source_port, transport)

    # Normalise the message at ingest: drop sequence numbers / device timestamps
    # so stored + displayed text is the actual log content. raw is preserved.
    result["message"] = clean_syslog_message(result.get("message") or "", result.get("hostname"))

    # FortiOS sends structured key=value logs (not Cisco mnemonics) — detect and
    # rewrite to a clean message + correct severity/program. raw stays original.
    if fortios.is_fortios_log(result["message"]):
        fortios.normalize(result, SEVERITIES)
    # AOS-CX emits a pipe-delimited "Event|id|LOG_…|module|-|msg" format.
    elif aos_cx.is_aos_cx_log(result["message"]):
        aos_cx.normalize(result, SEVERITIES)
    return result


# ── RFC 5424 ──────────────────────────────────────────────────────────────────

def _parse_rfc5424(
    raw: str,
    priority: int,
    rest: str,
    source_ip: str,
    source_port: int,
    transport: str,
) -> dict[str, Any]:
    m = _RFC5424_HEADER_RE.match(rest)
    if not m:
        return _raw_envelope(raw, source_ip, source_port, transport, priority)

    version = int(m.group(1))
    ts_raw = m.group(2)
    hostname = _nv(m.group(3))
    app_name = _nv(m.group(4))
    proc_id = _nv(m.group(5))
    msg_id = _nv(m.group(6))

    tail = rest[m.end():]

    # Structured data: either '-' or one-or-more '[SD-ID ...]' elements
    if tail.startswith("-"):
        sd: dict = {}
        tail = tail[1:]
    else:
        sd, tail = _parse_sd(tail)

    # Skip the single space that separates SD from MSG (RFC 5424 §6.4)
    message = tail.lstrip(" ").lstrip("\xef\xbb\xbf")  # strip BOM from MSG too

    facility, severity = divmod(priority, 8)
    return {
        "received_at": _utcnow(),
        "source_ip": source_ip,
        "source_port": source_port,
        "transport": transport,
        "facility": facility,
        "facility_name": FACILITIES.get(facility, f"local{facility - 16}"),
        "severity": severity,
        "severity_name": SEVERITIES.get(severity, str(severity)),
        "version": version,
        "timestamp": _parse_rfc5424_ts(ts_raw),
        "hostname": hostname or source_ip,
        "app_name": app_name,
        "proc_id": proc_id,
        "msg_id": msg_id,
        "structured_data": sd,
        "message": message,
        "raw": raw,
    }


def _parse_sd(s: str) -> tuple[dict[str, dict[str, str]], str]:
    """
    Parse one or more RFC 5424 SD-ELEMENTs from the start of s.
    Returns (structured_data_dict, remainder_string).
    """
    result: dict[str, dict[str, str]] = {}
    i = 0
    while i < len(s) and s[i] == "[":
        i += 1  # consume '['

        # SD-ID: read until space or ']'
        j = i
        while j < len(s) and s[j] not in (" ", "]"):
            j += 1
        sd_id = s[i:j]
        params: dict[str, str] = {}
        i = j

        # PARAM-VALUE pairs inside the element
        while i < len(s) and s[i] != "]":
            if s[i] == " ":
                i += 1
                continue

            # PARAM-NAME up to '='
            eq = s.find("=", i)
            if eq == -1 or ("]" in s[i:eq]):
                break
            param_name = s[i:eq]
            i = eq + 1

            # PARAM-VALUE must be double-quoted
            if i >= len(s) or s[i] != '"':
                break
            i += 1

            # Read value, honouring RFC 5424 escapes: \" \\ \]
            chars: list[str] = []
            while i < len(s):
                c = s[i]
                if c == "\\" and i + 1 < len(s) and s[i + 1] in ('"', "\\", "]"):
                    chars.append(s[i + 1])
                    i += 2
                elif c == '"':
                    i += 1
                    break
                else:
                    chars.append(c)
                    i += 1
            params[param_name] = "".join(chars)

        result[sd_id] = params
        if i < len(s) and s[i] == "]":
            i += 1  # consume ']'

    return result, s[i:]


# ── RFC 3164 ──────────────────────────────────────────────────────────────────

def _parse_rfc3164(
    raw: str,
    priority: int,
    rest: str,
    source_ip: str,
    source_port: int,
    transport: str,
) -> dict[str, Any]:
    facility, severity = divmod(priority, 8)
    base: dict[str, Any] = {
        "received_at": _utcnow(),
        "source_ip": source_ip,
        "source_port": source_port,
        "transport": transport,
        "facility": facility,
        "facility_name": FACILITIES.get(facility, f"local{facility - 16}"),
        "severity": severity,
        "severity_name": SEVERITIES.get(severity, str(severity)),
        "version": None,
        "timestamp": None,
        "hostname": source_ip,
        "app_name": None,
        "proc_id": None,
        "msg_id": None,
        "structured_data": {},
        "message": rest.strip(),
        "raw": raw,
    }

    m = _RFC3164_HEADER_RE.match(rest)
    if not m:
        return base

    base.update(
        timestamp=_parse_rfc3164_ts(m.group(1)),
        hostname=m.group(2) or source_ip,
        app_name=m.group(3),
        proc_id=m.group(4),
        message=(m.group(5) or "").strip(),
    )
    return base


# ── Fallback ──────────────────────────────────────────────────────────────────

def _raw_envelope(
    raw: str,
    source_ip: str,
    source_port: int,
    transport: str,
    priority: int | None = None,
) -> dict[str, Any]:
    facility = (priority >> 3) if priority is not None else None
    severity = (priority & 7) if priority is not None else None
    return {
        "received_at": _utcnow(),
        "source_ip": source_ip,
        "source_port": source_port,
        "transport": transport,
        "facility": facility,
        "facility_name": FACILITIES.get(facility, None) if facility is not None else None,
        "severity": severity,
        "severity_name": SEVERITIES.get(severity, None) if severity is not None else None,
        "version": None,
        "timestamp": None,
        "hostname": source_ip,
        "app_name": None,
        "proc_id": None,
        "msg_id": None,
        "structured_data": {},
        "message": raw,
        "raw": raw,
    }


# ── Timestamp helpers ─────────────────────────────────────────────────────────

def _parse_rfc5424_ts(ts: str) -> str | None:
    """Return the RFC 5424 timestamp string as-is (already ISO 8601) or None for '-'."""
    return None if ts == "-" else ts


def _parse_rfc3164_ts(ts: str) -> str | None:
    """
    Parse 'Mmm DD HH:MM:SS' (RFC 3164) → ISO 8601 string, inferring the year.

    Year rollover: if the parsed date is more than 1 day in the future,
    assume it belongs to the previous calendar year.
    """
    try:
        # Normalise variable whitespace: "Oct  1" → "Oct 1"
        norm = re.sub(r"\s+", " ", ts.strip())
        year = datetime.now().year
        dt = datetime.strptime(f"{year} {norm}", "%Y %b %d %H:%M:%S")
        now = datetime.now()
        if dt > now and (dt - now).days > 1:
            dt = dt.replace(year=year - 1)
        return dt.isoformat()
    except ValueError:
        return ts


# ── Misc helpers ──────────────────────────────────────────────────────────────

def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _nv(s: str) -> str | None:
    """Convert RFC 5424 NILVALUE '-' to None."""
    return None if s == "-" else s
