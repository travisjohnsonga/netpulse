"""
Handlers for log/trap streams → OpenSearch.

  netpulse.telemetry.{device_id}.trap  → netpulse-traps-YYYY.MM
  netpulse.otel.{device_id}.logs       → netpulse-otel-logs-YYYY.MM
  netpulse.vendor.>                    → netpulse-vendor-YYYY.MM

Also runs keyword anomaly detection and auth anomaly detection on syslog
records forwarded through the otel.logs topic.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from stream_processor.anomaly import auth as auth_anomaly
from stream_processor.anomaly import log_keywords

logger = logging.getLogger(__name__)


def _ts() -> str:
    return datetime.now(timezone.utc).isoformat()


def _monthly(prefix: str) -> str:
    return f"{prefix}-{datetime.now(timezone.utc).strftime('%Y.%m')}"


async def handle_trap(
    subject: str,
    data: dict,
    os_writer,  # OpenSearchWriter | None
) -> None:
    data["@timestamp"] = _ts()
    if os_writer:
        await os_writer.index(_monthly("netpulse-traps"), data)


async def handle_otel_logs(
    subject: str,
    data: dict,
    os_writer,  # OpenSearchWriter | None
    auth_detector: auth_anomaly.AuthAnomalyDetector | None,
) -> tuple[log_keywords.LogAnomaly | None, auth_anomaly.AuthAnomaly | None]:
    """
    Index the log in OpenSearch, run anomaly detectors.
    Returns (log_anomaly, auth_anomaly) — either may be None.
    """
    data["@timestamp"] = _ts()
    if os_writer:
        await os_writer.index(_monthly("netpulse-otel-logs"), data)

    kw_hit   = log_keywords.detect(data)
    auth_hit = auth_detector.feed(data) if auth_detector else None
    return kw_hit, auth_hit


async def handle_vendor(
    subject: str,
    data: dict,
    os_writer,  # OpenSearchWriter | None
) -> None:
    data["@timestamp"] = _ts()
    if os_writer:
        await os_writer.index(_monthly("netpulse-vendor"), data)
