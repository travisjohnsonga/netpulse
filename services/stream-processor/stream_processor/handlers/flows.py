"""
Handlers for NetFlow / sFlow and latency streams.

  netpulse.flows.{device_id}.{netflow5|netflow9|ipfix|sflow5}
      → OpenSearch index netpulse-flows-YYYY.MM
      → flow anomaly detection (high rate)

  netpulse.flows.{device_id}.latency
      → InfluxDB measurement transit_latency
      → latency anomaly detection
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from stream_processor.anomaly import flow as flow_anomaly
from stream_processor import config

logger = logging.getLogger(__name__)


def _monthly(prefix: str) -> str:
    return f"{prefix}-{datetime.now(timezone.utc).strftime('%Y.%m')}"


async def handle_flow(
    subject: str,
    data: dict,
    os_writer,   # OpenSearchWriter | None
    threshold_mbps: float = config.FLOW_THRESHOLD_MBPS,
) -> flow_anomaly.FlowAnomaly | None:
    """
    Index the flow record and return a FlowAnomaly if threshold exceeded.
    """
    data["@timestamp"] = datetime.now(timezone.utc).isoformat()
    if os_writer:
        await os_writer.index(_monthly("netpulse-flows"), data)
    return flow_anomaly.detect(data, threshold_mbps)


def handle_latency(
    subject: str,
    data: dict,
    influx,   # InfluxWriter | None
    threshold_ms: float = config.LATENCY_THRESHOLD_MS,
) -> bool:
    """
    Write latency to InfluxDB. Returns True if threshold exceeded.
    """
    latency = data.get("latency_ms", 0)
    if influx:
        influx.write(
            "transit_latency",
            {"src_device": data.get("src_device", ""), "dst_device": data.get("dst_device", "")},
            {"latency_ms": float(latency)},
        )
    return float(latency) > threshold_ms
