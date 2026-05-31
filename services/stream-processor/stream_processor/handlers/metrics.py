"""
Handler: netpulse.telemetry.{device_id}.metrics
         netpulse.otel.{device_id}.metrics

Writes time-series metrics to InfluxDB.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime

logger = logging.getLogger(__name__)

_SKIP_FIELDS = frozenset({"abs_start_time", "abs_end_time", "received_at", "exporter_ip"})


def _parse_ts(raw) -> float:
    """Best-effort sample time in epoch seconds from a payload timestamp."""
    if isinstance(raw, (int, float)):
        return float(raw)
    if isinstance(raw, str):
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
        except ValueError:
            pass
    return time.time()


def handle_telemetry_metrics(
    subject: str,
    data: dict,
    influx,  # InfluxWriter | None
    rate_calc=None,  # RateCalculator | None — derives bps/pps from counter deltas
) -> None:
    """Write SNMP/gNMI telemetry metrics to InfluxDB measurement 'telemetry'."""
    parts = subject.split(".")
    device_id = parts[2] if len(parts) > 2 else data.get("exporter_ip", "unknown")
    tags   = {"device_id": device_id}
    fields = {
        k: v for k, v in data.items()
        if isinstance(v, (int, float)) and k not in _SKIP_FIELDS
    }

    # Parse nested metrics dict from SNMP poller
    nested = data.get("metrics", {})
    for oid, m in nested.items():
        if not isinstance(m, dict):
            continue
        name = m.get("name", oid).replace(".", "_").replace("-", "_")
        val = m.get("value")
        mib_type = m.get("type", "")
        # Skip non-numeric values
        if not isinstance(val, (int, float)):
            try:
                val = float(val)
            except (TypeError, ValueError):
                continue
        # Convert TimeTicks to seconds
        if mib_type == "TimeTicks":
            val = float(val) / 100.0
        fields[name] = val

    # Derive interface throughput (bps/pps) from raw counter deltas. The raw
    # counters are still written above; these are the human-facing rates.
    if rate_calc is not None and nested:
        ts = _parse_ts(data.get("timestamp"))
        fields.update(rate_calc.compute(device_id, nested, ts))

    if not fields:
        logger.debug("no numeric fields in telemetry message from %s", device_id)
        return
    if influx:
        influx.write("telemetry", tags, fields)


def handle_otel_metrics(
    subject: str,
    data: dict,
    influx,  # InfluxWriter | None
) -> None:
    """Write OTLP metric data points to InfluxDB measurement 'otel_metrics'."""
    resource = data.get("resource_attrs", {})
    service  = resource.get("service.name", "unknown")
    metric   = data.get("metric_name", "")
    for dp in data.get("data_points", []):
        val = dp.get("value", dp.get("sum", None))
        if not isinstance(val, (int, float)):
            continue
        tags   = {"service": service, "metric": metric}
        attrs  = dp.get("attributes", {})
        for k, v in attrs.items():
            if isinstance(v, str):
                tags[k] = v
        if influx:
            influx.write("otel_metrics", tags, {"value": float(val)})
