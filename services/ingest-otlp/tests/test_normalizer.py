"""
Unit tests for ingest.normalizer.

Proto path tests are skipped if opentelemetry-proto is not installed.
JSON path tests always run (stdlib only).
"""
from __future__ import annotations

import json

import pytest

# ---------------------------------------------------------------------------
# Proto availability probe — used by skip markers
# ---------------------------------------------------------------------------

try:
    from opentelemetry.proto.collector.metrics.v1 import metrics_service_pb2 as _ms_pb2
    from opentelemetry.proto.collector.logs.v1 import logs_service_pb2 as _ls_pb2
    from opentelemetry.proto.collector.trace.v1 import trace_service_pb2 as _ts_pb2
    from opentelemetry.proto.metrics.v1 import metrics_pb2
    from opentelemetry.proto.logs.v1 import logs_pb2
    from opentelemetry.proto.trace.v1 import trace_pb2
    from opentelemetry.proto.common.v1 import common_pb2
    from opentelemetry.proto.resource.v1 import resource_pb2
    _PROTO_AVAILABLE = True
except ImportError:
    _PROTO_AVAILABLE = False

proto_only = pytest.mark.skipif(not _PROTO_AVAILABLE, reason="opentelemetry-proto not installed")


# ---------------------------------------------------------------------------
# Proto path tests
# ---------------------------------------------------------------------------

@proto_only
class TestParseMetricsProto:
    def _make_gauge_request(self) -> bytes:
        """Build a minimal ExportMetricsServiceRequest with one Gauge data point."""
        dp = metrics_pb2.NumberDataPoint(
            time_unix_nano=1_000_000_000,
            as_double=3.14,
        )
        dp.attributes.append(
            common_pb2.KeyValue(
                key="host",
                value=common_pb2.AnyValue(string_value="box1"),
            )
        )
        gauge = metrics_pb2.Gauge(data_points=[dp])
        metric = metrics_pb2.Metric(name="cpu.usage", unit="%", gauge=gauge)
        scope_metrics = metrics_pb2.ScopeMetrics(metrics=[metric])
        res = resource_pb2.Resource()
        res.attributes.append(
            common_pb2.KeyValue(
                key="service.name",
                value=common_pb2.AnyValue(string_value="my-service"),
            )
        )
        rm = metrics_pb2.ResourceMetrics(
            resource=res,
            scope_metrics=[scope_metrics],
        )
        req = _ms_pb2.ExportMetricsServiceRequest(resource_metrics=[rm])
        return req.SerializeToString()

    def test_parse_returns_list(self):
        from ingest.normalizer import parse_metrics
        data = self._make_gauge_request()
        result = parse_metrics(data, "10.0.0.1")
        assert isinstance(result, list)
        assert len(result) == 1

    def test_metric_name(self):
        from ingest.normalizer import parse_metrics
        item = parse_metrics(self._make_gauge_request(), "10.0.0.1")[0]
        assert item.metric_name == "cpu.usage"

    def test_metric_type_is_gauge(self):
        from ingest.normalizer import parse_metrics
        item = parse_metrics(self._make_gauge_request(), "10.0.0.1")[0]
        assert item.metric_type == "gauge"

    def test_unit(self):
        from ingest.normalizer import parse_metrics
        item = parse_metrics(self._make_gauge_request(), "10.0.0.1")[0]
        assert item.unit == "%"

    def test_resource_attrs(self):
        from ingest.normalizer import parse_metrics
        item = parse_metrics(self._make_gauge_request(), "10.0.0.1")[0]
        assert item.resource_attrs.get("service.name") == "my-service"

    def test_data_point_value(self):
        from ingest.normalizer import parse_metrics
        item = parse_metrics(self._make_gauge_request(), "10.0.0.1")[0]
        assert len(item.data_points) == 1
        assert item.data_points[0]["value"] == pytest.approx(3.14)

    def test_data_point_attribute(self):
        from ingest.normalizer import parse_metrics
        item = parse_metrics(self._make_gauge_request(), "10.0.0.1")[0]
        assert item.data_points[0]["attributes"].get("host") == "box1"

    def test_exporter_ip(self):
        from ingest.normalizer import parse_metrics
        item = parse_metrics(self._make_gauge_request(), "192.168.1.1")[0]
        assert item.exporter_ip == "192.168.1.1"


@proto_only
class TestParseLogsProto:
    def _make_log_request(self) -> bytes:
        lr = logs_pb2.LogRecord(
            time_unix_nano=2_000_000_000,
            severity_text="ERROR",
            severity_number=17,
            body=common_pb2.AnyValue(string_value="disk full"),
        )
        lr.trace_id = bytes(16)
        lr.span_id = bytes(8)
        scope_logs = logs_pb2.ScopeLogs(
            scope=common_pb2.InstrumentationScope(name="my.logger"),
            log_records=[lr],
        )
        res = resource_pb2.Resource()
        res.attributes.append(
            common_pb2.KeyValue(
                key="service.name",
                value=common_pb2.AnyValue(string_value="log-service"),
            )
        )
        rl = logs_pb2.ResourceLogs(resource=res, scope_logs=[scope_logs])
        req = _ls_pb2.ExportLogsServiceRequest(resource_logs=[rl])
        return req.SerializeToString()

    def test_parse_returns_list(self):
        from ingest.normalizer import parse_logs
        result = parse_logs(self._make_log_request(), "10.0.0.2")
        assert len(result) == 1

    def test_severity_text(self):
        from ingest.normalizer import parse_logs
        item = parse_logs(self._make_log_request(), "10.0.0.2")[0]
        assert item.severity_text == "ERROR"

    def test_severity_number(self):
        from ingest.normalizer import parse_logs
        item = parse_logs(self._make_log_request(), "10.0.0.2")[0]
        assert item.severity_number == 17

    def test_body(self):
        from ingest.normalizer import parse_logs
        item = parse_logs(self._make_log_request(), "10.0.0.2")[0]
        assert item.body == "disk full"

    def test_resource_attrs(self):
        from ingest.normalizer import parse_logs
        item = parse_logs(self._make_log_request(), "10.0.0.2")[0]
        assert item.resource_attrs.get("service.name") == "log-service"

    def test_scope_name(self):
        from ingest.normalizer import parse_logs
        item = parse_logs(self._make_log_request(), "10.0.0.2")[0]
        assert item.scope_name == "my.logger"

    def test_exporter_ip(self):
        from ingest.normalizer import parse_logs
        item = parse_logs(self._make_log_request(), "10.0.0.2")[0]
        assert item.exporter_ip == "10.0.0.2"


@proto_only
class TestParseTracesProto:
    def _make_trace_request(self) -> bytes:
        span = trace_pb2.Span(
            trace_id=b"\x01" * 16,
            span_id=b"\x02" * 8,
            parent_span_id=b"",
            name="GET /health",
            kind=trace_pb2.Span.SPAN_KIND_SERVER,
            start_time_unix_nano=1_000_000_000,
            end_time_unix_nano=1_250_000_000,
        )
        span.attributes.append(
            common_pb2.KeyValue(
                key="http.method",
                value=common_pb2.AnyValue(string_value="GET"),
            )
        )
        scope_spans = trace_pb2.ScopeSpans(
            scope=common_pb2.InstrumentationScope(name="my.tracer"),
            spans=[span],
        )
        res = resource_pb2.Resource()
        res.attributes.append(
            common_pb2.KeyValue(
                key="service.name",
                value=common_pb2.AnyValue(string_value="trace-service"),
            )
        )
        rs = trace_pb2.ResourceSpans(resource=res, scope_spans=[scope_spans])
        req = _ts_pb2.ExportTraceServiceRequest(resource_spans=[rs])
        return req.SerializeToString()

    def test_parse_returns_list(self):
        from ingest.normalizer import parse_traces
        result = parse_traces(self._make_trace_request(), "10.0.0.3")
        assert len(result) == 1

    def test_span_name(self):
        from ingest.normalizer import parse_traces
        item = parse_traces(self._make_trace_request(), "10.0.0.3")[0]
        assert item.name == "GET /health"

    def test_duration_ms(self):
        from ingest.normalizer import parse_traces
        item = parse_traces(self._make_trace_request(), "10.0.0.3")[0]
        # 1_250_000_000 - 1_000_000_000 = 250_000_000 ns = 250 ms
        assert item.duration_ms == pytest.approx(250.0)

    def test_trace_id_hex(self):
        from ingest.normalizer import parse_traces
        item = parse_traces(self._make_trace_request(), "10.0.0.3")[0]
        assert item.trace_id == "01" * 16

    def test_span_id_hex(self):
        from ingest.normalizer import parse_traces
        item = parse_traces(self._make_trace_request(), "10.0.0.3")[0]
        assert item.span_id == "02" * 8

    def test_kind(self):
        from ingest.normalizer import parse_traces
        item = parse_traces(self._make_trace_request(), "10.0.0.3")[0]
        assert item.kind == trace_pb2.Span.SPAN_KIND_SERVER

    def test_resource_attrs(self):
        from ingest.normalizer import parse_traces
        item = parse_traces(self._make_trace_request(), "10.0.0.3")[0]
        assert item.resource_attrs.get("service.name") == "trace-service"

    def test_attributes(self):
        from ingest.normalizer import parse_traces
        item = parse_traces(self._make_trace_request(), "10.0.0.3")[0]
        assert item.attributes.get("http.method") == "GET"

    def test_exporter_ip(self):
        from ingest.normalizer import parse_traces
        item = parse_traces(self._make_trace_request(), "10.0.0.3")[0]
        assert item.exporter_ip == "10.0.0.3"


# ---------------------------------------------------------------------------
# JSON path tests — always run, no proto dependency
# ---------------------------------------------------------------------------

class TestParseMetricsJSON:
    def _payload(self, metric_type="gauge", value=1.5) -> bytes:
        payload = {
            "resourceMetrics": [
                {
                    "resource": {
                        "attributes": [
                            {"key": "service.name", "value": {"stringValue": "json-svc"}}
                        ]
                    },
                    "scopeMetrics": [
                        {
                            "scope": {"name": "json.scope"},
                            "metrics": [
                                {
                                    "name": "requests.total",
                                    "unit": "1",
                                    metric_type: {
                                        "dataPoints": [
                                            {
                                                "attributes": [
                                                    {"key": "env", "value": {"stringValue": "prod"}}
                                                ],
                                                "timeUnixNano": "1000000000",
                                                "asDouble": value,
                                            }
                                        ]
                                    },
                                }
                            ],
                        }
                    ],
                }
            ]
        }
        return json.dumps(payload).encode()

    def test_returns_list(self):
        from ingest.normalizer import parse_metrics_json
        result = parse_metrics_json(self._payload(), "10.1.0.1")
        assert isinstance(result, list)
        assert len(result) == 1

    def test_metric_name(self):
        from ingest.normalizer import parse_metrics_json
        item = parse_metrics_json(self._payload(), "10.1.0.1")[0]
        assert item.metric_name == "requests.total"

    def test_metric_type_gauge(self):
        from ingest.normalizer import parse_metrics_json
        item = parse_metrics_json(self._payload(metric_type="gauge"), "10.1.0.1")[0]
        assert item.metric_type == "gauge"

    def test_metric_type_sum(self):
        from ingest.normalizer import parse_metrics_json
        item = parse_metrics_json(self._payload(metric_type="sum"), "10.1.0.1")[0]
        assert item.metric_type == "sum"

    def test_data_point_value(self):
        from ingest.normalizer import parse_metrics_json
        item = parse_metrics_json(self._payload(value=99.9), "10.1.0.1")[0]
        assert item.data_points[0]["value"] == pytest.approx(99.9)

    def test_resource_attrs(self):
        from ingest.normalizer import parse_metrics_json
        item = parse_metrics_json(self._payload(), "10.1.0.1")[0]
        assert item.resource_attrs.get("service.name") == "json-svc"

    def test_data_point_attribute(self):
        from ingest.normalizer import parse_metrics_json
        item = parse_metrics_json(self._payload(), "10.1.0.1")[0]
        assert item.data_points[0]["attributes"].get("env") == "prod"

    def test_exporter_ip(self):
        from ingest.normalizer import parse_metrics_json
        item = parse_metrics_json(self._payload(), "172.16.0.1")[0]
        assert item.exporter_ip == "172.16.0.1"

    def test_unit(self):
        from ingest.normalizer import parse_metrics_json
        item = parse_metrics_json(self._payload(), "10.1.0.1")[0]
        assert item.unit == "1"

    def test_scope_name(self):
        from ingest.normalizer import parse_metrics_json
        item = parse_metrics_json(self._payload(), "10.1.0.1")[0]
        assert item.scope_name == "json.scope"

    def test_empty_payload(self):
        from ingest.normalizer import parse_metrics_json
        result = parse_metrics_json(b'{"resourceMetrics":[]}', "10.1.0.1")
        assert result == []

    def test_histogram_type(self):
        from ingest.normalizer import parse_metrics_json
        payload = {
            "resourceMetrics": [
                {
                    "resource": {"attributes": []},
                    "scopeMetrics": [
                        {
                            "scope": {"name": ""},
                            "metrics": [
                                {
                                    "name": "latency",
                                    "unit": "ms",
                                    "histogram": {
                                        "dataPoints": [
                                            {
                                                "attributes": [],
                                                "timeUnixNano": "5000",
                                                "count": "10",
                                                "sum": 500.0,
                                                "bucketCounts": ["2", "5", "3"],
                                                "explicitBounds": [10.0, 100.0],
                                            }
                                        ]
                                    },
                                }
                            ],
                        }
                    ],
                }
            ]
        }
        items = parse_metrics_json(json.dumps(payload).encode(), "10.1.0.1")
        assert len(items) == 1
        assert items[0].metric_type == "histogram"
        assert items[0].data_points[0]["count"] == "10"


class TestParseLogsJSON:
    def _payload(self, severity_text="WARN", severity_number=13, body="test log") -> bytes:
        payload = {
            "resourceLogs": [
                {
                    "resource": {
                        "attributes": [
                            {"key": "service.name", "value": {"stringValue": "log-app"}}
                        ]
                    },
                    "scopeLogs": [
                        {
                            "scope": {"name": "my.logger"},
                            "logRecords": [
                                {
                                    "timeUnixNano": "3000000000",
                                    "severityText": severity_text,
                                    "severityNumber": severity_number,
                                    "body": {"stringValue": body},
                                    "attributes": [
                                        {"key": "error.type", "value": {"stringValue": "ValueError"}}
                                    ],
                                    "traceId": "aabbccdd",
                                    "spanId": "11223344",
                                }
                            ],
                        }
                    ],
                }
            ]
        }
        return json.dumps(payload).encode()

    def test_returns_list(self):
        from ingest.normalizer import parse_logs_json
        assert len(parse_logs_json(self._payload(), "10.2.0.1")) == 1

    def test_severity_text(self):
        from ingest.normalizer import parse_logs_json
        item = parse_logs_json(self._payload(severity_text="ERROR"), "10.2.0.1")[0]
        assert item.severity_text == "ERROR"

    def test_severity_number(self):
        from ingest.normalizer import parse_logs_json
        item = parse_logs_json(self._payload(severity_number=17), "10.2.0.1")[0]
        assert item.severity_number == 17

    def test_body(self):
        from ingest.normalizer import parse_logs_json
        item = parse_logs_json(self._payload(body="hello"), "10.2.0.1")[0]
        assert item.body == "hello"

    def test_trace_id(self):
        from ingest.normalizer import parse_logs_json
        item = parse_logs_json(self._payload(), "10.2.0.1")[0]
        assert item.trace_id == "aabbccdd"

    def test_span_id(self):
        from ingest.normalizer import parse_logs_json
        item = parse_logs_json(self._payload(), "10.2.0.1")[0]
        assert item.span_id == "11223344"

    def test_resource_attrs(self):
        from ingest.normalizer import parse_logs_json
        item = parse_logs_json(self._payload(), "10.2.0.1")[0]
        assert item.resource_attrs.get("service.name") == "log-app"

    def test_attributes(self):
        from ingest.normalizer import parse_logs_json
        item = parse_logs_json(self._payload(), "10.2.0.1")[0]
        assert item.attributes.get("error.type") == "ValueError"

    def test_exporter_ip(self):
        from ingest.normalizer import parse_logs_json
        item = parse_logs_json(self._payload(), "10.2.0.2")[0]
        assert item.exporter_ip == "10.2.0.2"

    def test_empty_payload(self):
        from ingest.normalizer import parse_logs_json
        assert parse_logs_json(b'{"resourceLogs":[]}', "10.2.0.1") == []


class TestParseTracesJSON:
    def _payload(
        self,
        name="GET /api",
        kind=2,
        start_ns=1_000_000_000,
        end_ns=1_500_000_000,
        status_code=1,
    ) -> bytes:
        payload = {
            "resourceSpans": [
                {
                    "resource": {
                        "attributes": [
                            {"key": "service.name", "value": {"stringValue": "trace-app"}}
                        ]
                    },
                    "scopeSpans": [
                        {
                            "scope": {"name": "my.tracer"},
                            "spans": [
                                {
                                    "traceId": "trace-001",
                                    "spanId": "span-001",
                                    "parentSpanId": "",
                                    "name": name,
                                    "kind": kind,
                                    "startTimeUnixNano": str(start_ns),
                                    "endTimeUnixNano": str(end_ns),
                                    "attributes": [
                                        {"key": "http.method", "value": {"stringValue": "GET"}}
                                    ],
                                    "status": {"code": status_code},
                                }
                            ],
                        }
                    ],
                }
            ]
        }
        return json.dumps(payload).encode()

    def test_returns_list(self):
        from ingest.normalizer import parse_traces_json
        assert len(parse_traces_json(self._payload(), "10.3.0.1")) == 1

    def test_span_name(self):
        from ingest.normalizer import parse_traces_json
        item = parse_traces_json(self._payload(name="POST /submit"), "10.3.0.1")[0]
        assert item.name == "POST /submit"

    def test_duration_ms(self):
        from ingest.normalizer import parse_traces_json
        # 1_500_000_000 - 1_000_000_000 = 500_000_000 ns = 500 ms
        item = parse_traces_json(self._payload(start_ns=1_000_000_000, end_ns=1_500_000_000), "10.3.0.1")[0]
        assert item.duration_ms == pytest.approx(500.0)

    def test_trace_id(self):
        from ingest.normalizer import parse_traces_json
        item = parse_traces_json(self._payload(), "10.3.0.1")[0]
        assert item.trace_id == "trace-001"

    def test_span_id(self):
        from ingest.normalizer import parse_traces_json
        item = parse_traces_json(self._payload(), "10.3.0.1")[0]
        assert item.span_id == "span-001"

    def test_kind(self):
        from ingest.normalizer import parse_traces_json
        item = parse_traces_json(self._payload(kind=3), "10.3.0.1")[0]
        assert item.kind == 3

    def test_status_code(self):
        from ingest.normalizer import parse_traces_json
        item = parse_traces_json(self._payload(status_code=2), "10.3.0.1")[0]
        assert item.status_code == 2

    def test_resource_attrs(self):
        from ingest.normalizer import parse_traces_json
        item = parse_traces_json(self._payload(), "10.3.0.1")[0]
        assert item.resource_attrs.get("service.name") == "trace-app"

    def test_attributes(self):
        from ingest.normalizer import parse_traces_json
        item = parse_traces_json(self._payload(), "10.3.0.1")[0]
        assert item.attributes.get("http.method") == "GET"

    def test_exporter_ip(self):
        from ingest.normalizer import parse_traces_json
        item = parse_traces_json(self._payload(), "172.20.0.5")[0]
        assert item.exporter_ip == "172.20.0.5"

    def test_zero_duration(self):
        from ingest.normalizer import parse_traces_json
        item = parse_traces_json(self._payload(start_ns=100, end_ns=100), "10.3.0.1")[0]
        assert item.duration_ms == 0.0

    def test_empty_payload(self):
        from ingest.normalizer import parse_traces_json
        assert parse_traces_json(b'{"resourceSpans":[]}', "10.3.0.1") == []
