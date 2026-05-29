"""
Handler: netpulse.telemetry.{device_id}.metrics
         netpulse.otel.{device_id}.metrics

Writes time-series metrics to InfluxDB.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_SKIP_FIELDS = frozenset({"abs_start_time", "abs_end_time", "received_at", "exporter_ip"})


def handle_telemetry_metrics(
    subject: str,
    data: dict,
    influx,  # InfluxWriter | None
) -> None:
    """Write SNMP/gNMI telemetry metrics to InfluxDB measurement 'telemetry'."""
    parts = subject.split(".")
    device_id = parts[2] if len(parts) > 2 else data.get("exporter_ip", "unknown")
    tags   = {"device_id": device_id}
    fields = {
        k: v for k, v in data.items()
        if isinstance(v, (int, float)) and k not in _SKIP_FIELDS
    }
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
