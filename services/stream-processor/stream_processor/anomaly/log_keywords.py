"""Log keyword anomaly detection: pattern match against syslog/OTLP log bodies."""
from __future__ import annotations

import re
from dataclasses import dataclass

_KEYWORD_RE = re.compile(r"\b(error|critical|down|unreachable|fail(?:ed|ure)?)\b", re.I)


@dataclass
class LogAnomaly:
    exporter_ip: str
    body: str
    matched_keyword: str
    message: str


def detect(record: dict) -> LogAnomaly | None:
    """Return a LogAnomaly if the log body contains a critical keyword, else None."""
    body = record.get("body", "") or record.get("message", "")
    if not body:
        return None
    m = _KEYWORD_RE.search(body)
    if not m:
        return None
    exporter = record.get("exporter_ip", record.get("source_ip", ""))
    return LogAnomaly(
        exporter_ip=exporter,
        body=body[:500],
        matched_keyword=m.group(0).lower(),
        message=body[:200],
    )
