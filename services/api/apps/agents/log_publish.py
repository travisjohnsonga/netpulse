"""Relay agent-forwarded raw log lines onto NATS ``netpulse.logs.<source>.<host>``
so the EXISTING pipeline (stream-processor → OpenSearch netpulse-logs-* → Logs UI)
ingests them — no new storage. The agent ships raw lines over mTLS; ALL parsing
is server-side (Stage 2). Mirrors devices/snmp_publish.py's best-effort
sync→async NATS pattern (NATS is bridge-internal; the agent never touches it).
"""
import asyncio
import json
import logging
import os
import re
from datetime import datetime, timezone

from django.conf import settings

logger = logging.getLogger(__name__)

# Stage-1 curated security-profile sources + the additional_paths escape hatch.
ALLOWED_LOG_SOURCES = {"auth", "service", "kernel", "custom"}

_TOKEN_RE = re.compile(r"[^A-Za-z0-9_-]")  # keep NATS subject tokens clean


def _token(s: str) -> str:
    return _TOKEN_RE.sub("_", s) or "unknown"


async def _connect():
    import nats  # lazy
    return await nats.connect(
        os.environ.get("NATS_URL", getattr(settings, "NATS_URL", "nats://nats:4222")),
        user=os.environ.get("NATS_USER", getattr(settings, "NATS_USER", "")) or None,
        password=os.environ.get("NATS_PASSWORD", getattr(settings, "NATS_PASSWORD", "")) or None,
        connect_timeout=3,
    )


async def _publish(source: str, hostname: str, lines: list[str]) -> None:
    nc = await _connect()
    subject = f"netpulse.logs.{_token(source)}.{_token(hostname)}"
    ts = datetime.now(timezone.utc).isoformat()
    try:
        for line in lines:
            payload = {
                "message": line,        # the raw log line (the canonical body field)
                "hostname": hostname,
                "timestamp": ts,
                "log_source": source,   # auth/service/kernel/custom (also in the subject)
                "agent": True,
            }
            await nc.publish(subject, json.dumps(payload).encode())
        await nc.flush()
    finally:
        await nc.drain()


def publish_log_lines(source: str, hostname: str, lines) -> int:
    """Publish each non-empty raw line to NATS. Best-effort: returns the number
    published, 0 on failure (logged) — a NATS hiccup never breaks the agent POST."""
    clean = [str(x) for x in (lines or []) if str(x).strip()]
    if not clean:
        return 0
    try:
        asyncio.run(_publish(source, hostname, clean))
        return len(clean)
    except Exception as exc:  # NATS down, etc.
        logger.warning("NATS log relay failed (%d lines, source=%s): %s", len(clean), source, exc)
        return 0
