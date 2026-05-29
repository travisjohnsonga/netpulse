"""
OTLP payload normaliser.

Supports two code paths:
  1. Protobuf bytes  — via opentelemetry-proto generated message classes.
  2. JSON bytes      — via stdlib json (OTLP HTTP/JSON export format).

If the opentelemetry-proto package is not installed the proto path will
raise ImportError at call time; callers should send JSON in that case or
handle the exception.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from .models import OTLPLog, OTLPMetric, OTLPTrace

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_utc() -> datetime:
    return datetime.now(tz=timezone.utc)


def _bytes_to_hex(b: bytes | str) -> str:
    if isinstance(b, (bytes, bytearray)):
        return b.hex()
    return str(b)


def _kv_list_to_dict(kv_list) -> dict:
    """Convert a repeated AnyValue KeyValue list to a plain dict."""
    out = {}
    for kv in kv_list:
        out[kv.key] = _any_value(kv.value)
    return out


def _any_value(av) -> object:
    """Unwrap an opentelemetry AnyValue message."""
    kind = av.WhichOneof("value")
    if kind == "string_value":
        return av.string_value
    if kind == "bool_value":
        return av.bool_value
    if kind == "int_value":
        return av.int_value
    if kind == "double_value":
        return av.double_value
    if kind == "bytes_value":
        return _bytes_to_hex(av.bytes_value)
    if kind == "array_value":
        return [_any_value(v) for v in av.array_value.values]
    if kind == "kvlist_value":
        return _kv_list_to_dict(av.kvlist_value.values)
    return None


# ---------------------------------------------------------------------------
# Metric data-point extraction helpers
# ---------------------------------------------------------------------------

def _gauge_points(gauge) -> list[dict]:
    points = []
    for dp in gauge.data_points:
        kind = dp.WhichOneof("value")
        value = getattr(dp, kind) if kind else None
        points.append({
            "attributes": _kv_list_to_dict(dp.attributes),
            "time_unix_nano": dp.time_unix_nano,
            "value": value,
        })
    return points


def _sum_points(s) -> list[dict]:
    points = []
    for dp in s.data_points:
        kind = dp.WhichOneof("value")
        value = getattr(dp, kind) if kind else None
        points.append({
            "attributes": _kv_list_to_dict(dp.attributes),
            "time_unix_nano": dp.time_unix_nano,
            "value": value,
            "aggregation_temporality": s.aggregation_temporality,
            "is_monotonic": s.is_monotonic,
        })
    return points


def _histogram_points(hist) -> list[dict]:
    points = []
    for dp in hist.data_points:
        points.append({
            "attributes": _kv_list_to_dict(dp.attributes),
            "time_unix_nano": dp.time_unix_nano,
            "count": dp.count,
            "sum": dp.sum if dp.HasField("sum") else None,
            "bucket_counts": list(dp.bucket_counts),
            "explicit_bounds": list(dp.explicit_bounds),
        })
    return points


def _summary_points(summ) -> list[dict]:
    points = []
    for dp in summ.data_points:
        points.append({
            "attributes": _kv_list_to_dict(dp.attributes),
            "time_unix_nano": dp.time_unix_nano,
            "count": dp.count,
            "sum": dp.sum,
            "quantile_values": [
                {"quantile": qv.quantile, "value": qv.value}
                for qv in dp.quantile_values
            ],
        })
    return points


# ---------------------------------------------------------------------------
# Public parse functions — protobuf path
# ---------------------------------------------------------------------------

def parse_metrics(data: bytes, exporter_ip: str) -> list[OTLPMetric]:
    """Parse raw protobuf bytes from an OTLP ExportMetricsServiceRequest."""
    from opentelemetry.proto.collector.metrics.v1 import metrics_service_pb2  # noqa: PLC0415

    req = metrics_service_pb2.ExportMetricsServiceRequest()
    req.ParseFromString(data)

    results: list[OTLPMetric] = []
    received_at = _now_utc()

    for rm in req.resource_metrics:
        resource_attrs = _kv_list_to_dict(rm.resource.attributes)
        for sm in rm.scope_metrics:
            scope_name = sm.scope.name
            for metric in sm.metrics:
                kind = metric.WhichOneof("data")
                if kind == "gauge":
                    metric_type = "gauge"
                    data_points = _gauge_points(metric.gauge)
                elif kind == "sum":
                    metric_type = "sum"
                    data_points = _sum_points(metric.sum)
                elif kind == "histogram":
                    metric_type = "histogram"
                    data_points = _histogram_points(metric.histogram)
                elif kind == "summary":
                    metric_type = "summary"
                    data_points = _summary_points(metric.summary)
                else:
                    logger.debug("unknown metric kind %r in %s", kind, metric.name)
                    continue

                results.append(OTLPMetric(
                    resource_attrs=resource_attrs,
                    scope_name=scope_name,
                    metric_name=metric.name,
                    metric_type=metric_type,
                    unit=metric.unit,
                    data_points=data_points,
                    exporter_ip=exporter_ip,
                    received_at=received_at,
                ))

    return results


def parse_logs(data: bytes, exporter_ip: str) -> list[OTLPLog]:
    """Parse raw protobuf bytes from an OTLP ExportLogsServiceRequest."""
    from opentelemetry.proto.collector.logs.v1 import logs_service_pb2  # noqa: PLC0415

    req = logs_service_pb2.ExportLogsServiceRequest()
    req.ParseFromString(data)

    results: list[OTLPLog] = []
    received_at = _now_utc()

    for rl in req.resource_logs:
        resource_attrs = _kv_list_to_dict(rl.resource.attributes)
        for sl in rl.scope_logs:
            scope_name = sl.scope.name
            for lr in sl.log_records:
                body_kind = lr.body.WhichOneof("value")
                body = getattr(lr.body, body_kind) if body_kind else ""
                if isinstance(body, bytes):
                    body = body.decode("utf-8", errors="replace")

                results.append(OTLPLog(
                    resource_attrs=resource_attrs,
                    scope_name=scope_name,
                    severity_text=lr.severity_text,
                    severity_number=lr.severity_number,
                    body=str(body),
                    attributes=_kv_list_to_dict(lr.attributes),
                    trace_id=_bytes_to_hex(lr.trace_id),
                    span_id=_bytes_to_hex(lr.span_id),
                    timestamp_unix_nano=lr.time_unix_nano,
                    exporter_ip=exporter_ip,
                    received_at=received_at,
                ))

    return results


def parse_traces(data: bytes, exporter_ip: str) -> list[OTLPTrace]:
    """Parse raw protobuf bytes from an OTLP ExportTraceServiceRequest."""
    from opentelemetry.proto.collector.trace.v1 import trace_service_pb2  # noqa: PLC0415

    req = trace_service_pb2.ExportTraceServiceRequest()
    req.ParseFromString(data)

    results: list[OTLPTrace] = []
    received_at = _now_utc()

    for rs in req.resource_spans:
        resource_attrs = _kv_list_to_dict(rs.resource.attributes)
        for ss in rs.scope_spans:
            scope_name = ss.scope.name
            for span in ss.spans:
                duration_ns = span.end_time_unix_nano - span.start_time_unix_nano
                duration_ms = duration_ns / 1_000_000

                # status code: 0=UNSET, 1=OK, 2=ERROR
                status_code = span.status.code if span.HasField("status") else 0

                results.append(OTLPTrace(
                    resource_attrs=resource_attrs,
                    scope_name=scope_name,
                    trace_id=_bytes_to_hex(span.trace_id),
                    span_id=_bytes_to_hex(span.span_id),
                    parent_span_id=_bytes_to_hex(span.parent_span_id),
                    name=span.name,
                    kind=span.kind,
                    start_time_unix_nano=span.start_time_unix_nano,
                    end_time_unix_nano=span.end_time_unix_nano,
                    duration_ms=duration_ms,
                    attributes=_kv_list_to_dict(span.attributes),
                    status_code=status_code,
                    exporter_ip=exporter_ip,
                    received_at=received_at,
                ))

    return results


# ---------------------------------------------------------------------------
# JSON path (OTLP HTTP/JSON)
# ---------------------------------------------------------------------------

def _json_attrs(attrs: list[dict]) -> dict:
    """Convert OTLP JSON attribute list [{key, value: {stringValue/intValue/...}}] to dict."""
    out = {}
    for item in attrs or []:
        key = item.get("key", "")
        val_obj = item.get("value", {})
        if not val_obj:
            out[key] = None
            continue
        # Pick whichever typed key is present
        for typed_key in ("stringValue", "intValue", "doubleValue", "boolValue",
                          "bytesValue", "arrayValue", "kvlistValue"):
            if typed_key in val_obj:
                out[key] = val_obj[typed_key]
                break
        else:
            out[key] = None
    return out


def parse_metrics_json(data: bytes, exporter_ip: str) -> list[OTLPMetric]:
    """Parse OTLP JSON metrics payload."""
    payload = json.loads(data)
    results: list[OTLPMetric] = []
    received_at = _now_utc()

    for rm in payload.get("resourceMetrics", []):
        resource_attrs = _json_attrs(rm.get("resource", {}).get("attributes", []))
        for sm in rm.get("scopeMetrics", []):
            scope_name = sm.get("scope", {}).get("name", "")
            for metric in sm.get("metrics", []):
                metric_name = metric.get("name", "")
                unit = metric.get("unit", "")
                data_points: list[dict] = []
                metric_type = "unknown"

                if "gauge" in metric:
                    metric_type = "gauge"
                    for dp in metric["gauge"].get("dataPoints", []):
                        data_points.append({
                            "attributes": _json_attrs(dp.get("attributes", [])),
                            "time_unix_nano": int(dp.get("timeUnixNano", 0)),
                            "value": dp.get("asDouble") or dp.get("asInt"),
                        })
                elif "sum" in metric:
                    metric_type = "sum"
                    for dp in metric["sum"].get("dataPoints", []):
                        data_points.append({
                            "attributes": _json_attrs(dp.get("attributes", [])),
                            "time_unix_nano": int(dp.get("timeUnixNano", 0)),
                            "value": dp.get("asDouble") or dp.get("asInt"),
                        })
                elif "histogram" in metric:
                    metric_type = "histogram"
                    for dp in metric["histogram"].get("dataPoints", []):
                        data_points.append({
                            "attributes": _json_attrs(dp.get("attributes", [])),
                            "time_unix_nano": int(dp.get("timeUnixNano", 0)),
                            "count": dp.get("count"),
                            "sum": dp.get("sum"),
                            "bucket_counts": dp.get("bucketCounts", []),
                            "explicit_bounds": dp.get("explicitBounds", []),
                        })
                elif "summary" in metric:
                    metric_type = "summary"
                    for dp in metric["summary"].get("dataPoints", []):
                        data_points.append({
                            "attributes": _json_attrs(dp.get("attributes", [])),
                            "time_unix_nano": int(dp.get("timeUnixNano", 0)),
                            "count": dp.get("count"),
                            "sum": dp.get("sum"),
                            "quantile_values": dp.get("quantileValues", []),
                        })

                results.append(OTLPMetric(
                    resource_attrs=resource_attrs,
                    scope_name=scope_name,
                    metric_name=metric_name,
                    metric_type=metric_type,
                    unit=unit,
                    data_points=data_points,
                    exporter_ip=exporter_ip,
                    received_at=received_at,
                ))

    return results


def parse_logs_json(data: bytes, exporter_ip: str) -> list[OTLPLog]:
    """Parse OTLP JSON logs payload."""
    payload = json.loads(data)
    results: list[OTLPLog] = []
    received_at = _now_utc()

    for rl in payload.get("resourceLogs", []):
        resource_attrs = _json_attrs(rl.get("resource", {}).get("attributes", []))
        for sl in rl.get("scopeLogs", []):
            scope_name = sl.get("scope", {}).get("name", "")
            for lr in sl.get("logRecords", []):
                body_val = lr.get("body", {})
                body = body_val.get("stringValue", str(body_val)) if isinstance(body_val, dict) else str(body_val)

                results.append(OTLPLog(
                    resource_attrs=resource_attrs,
                    scope_name=scope_name,
                    severity_text=lr.get("severityText", ""),
                    severity_number=int(lr.get("severityNumber", 0)),
                    body=body,
                    attributes=_json_attrs(lr.get("attributes", [])),
                    trace_id=lr.get("traceId", ""),
                    span_id=lr.get("spanId", ""),
                    timestamp_unix_nano=int(lr.get("timeUnixNano", 0)),
                    exporter_ip=exporter_ip,
                    received_at=received_at,
                ))

    return results


def parse_traces_json(data: bytes, exporter_ip: str) -> list[OTLPTrace]:
    """Parse OTLP JSON traces payload."""
    payload = json.loads(data)
    results: list[OTLPTrace] = []
    received_at = _now_utc()

    for rs in payload.get("resourceSpans", []):
        resource_attrs = _json_attrs(rs.get("resource", {}).get("attributes", []))
        for ss in rs.get("scopeSpans", []):
            scope_name = ss.get("scope", {}).get("name", "")
            for span in ss.get("spans", []):
                start_ns = int(span.get("startTimeUnixNano", 0))
                end_ns = int(span.get("endTimeUnixNano", 0))
                duration_ms = (end_ns - start_ns) / 1_000_000

                results.append(OTLPTrace(
                    resource_attrs=resource_attrs,
                    scope_name=scope_name,
                    trace_id=span.get("traceId", ""),
                    span_id=span.get("spanId", ""),
                    parent_span_id=span.get("parentSpanId", ""),
                    name=span.get("name", ""),
                    kind=int(span.get("kind", 0)),
                    start_time_unix_nano=start_ns,
                    end_time_unix_nano=end_ns,
                    duration_ms=duration_ms,
                    attributes=_json_attrs(span.get("attributes", [])),
                    status_code=int(span.get("status", {}).get("code", 0)),
                    exporter_ip=exporter_ip,
                    received_at=received_at,
                ))

    return results
