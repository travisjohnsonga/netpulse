"""
Unit tests for ingest.models — to_dict() methods.

No external dependencies required beyond the standard library.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from ingest.models import OTLPLog, OTLPMetric, OTLPTrace


_TS = datetime(2024, 1, 15, 12, 0, 0, tzinfo=timezone.utc)


class TestOTLPMetricToDict:
    def _make(self, **kwargs) -> OTLPMetric:
        defaults = dict(
            resource_attrs={"service.name": "my-app"},
            scope_name="my.library",
            metric_name="http.requests",
            metric_type="sum",
            unit="1",
            data_points=[{"attributes": {}, "time_unix_nano": 1_000_000, "value": 42}],
            exporter_ip="10.0.0.1",
            received_at=_TS,
        )
        defaults.update(kwargs)
        return OTLPMetric(**defaults)

    def test_to_dict_returns_dict(self):
        d = self._make().to_dict()
        assert isinstance(d, dict)

    def test_metric_name_preserved(self):
        d = self._make(metric_name="cpu.usage").to_dict()
        assert d["metric_name"] == "cpu.usage"

    def test_metric_type_preserved(self):
        d = self._make(metric_type="gauge").to_dict()
        assert d["metric_type"] == "gauge"

    def test_exporter_ip_preserved(self):
        d = self._make(exporter_ip="192.168.1.50").to_dict()
        assert d["exporter_ip"] == "192.168.1.50"

    def test_received_at_is_isoformat_string(self):
        d = self._make().to_dict()
        assert isinstance(d["received_at"], str)
        assert "2024-01-15" in d["received_at"]

    def test_resource_attrs_preserved(self):
        attrs = {"service.name": "svc", "host.name": "box1"}
        d = self._make(resource_attrs=attrs).to_dict()
        assert d["resource_attrs"] == attrs

    def test_data_points_preserved(self):
        dps = [{"attributes": {"region": "us-east"}, "time_unix_nano": 999, "value": 7.5}]
        d = self._make(data_points=dps).to_dict()
        assert d["data_points"] == dps

    def test_unit_preserved(self):
        d = self._make(unit="ms").to_dict()
        assert d["unit"] == "ms"

    def test_scope_name_preserved(self):
        d = self._make(scope_name="opentelemetry.instrumentation.django").to_dict()
        assert d["scope_name"] == "opentelemetry.instrumentation.django"


class TestOTLPLogToDict:
    def _make(self, **kwargs) -> OTLPLog:
        defaults = dict(
            resource_attrs={"service.name": "logger-app"},
            scope_name="my.logger",
            severity_text="ERROR",
            severity_number=17,
            body="something went wrong",
            attributes={"error.type": "ValueError"},
            trace_id="abc123",
            span_id="def456",
            timestamp_unix_nano=1_700_000_000_000_000_000,
            exporter_ip="10.0.0.2",
            received_at=_TS,
        )
        defaults.update(kwargs)
        return OTLPLog(**defaults)

    def test_to_dict_returns_dict(self):
        assert isinstance(self._make().to_dict(), dict)

    def test_severity_text_preserved(self):
        d = self._make(severity_text="WARN").to_dict()
        assert d["severity_text"] == "WARN"

    def test_severity_number_preserved(self):
        d = self._make(severity_number=9).to_dict()
        assert d["severity_number"] == 9

    def test_body_preserved(self):
        d = self._make(body="hello world").to_dict()
        assert d["body"] == "hello world"

    def test_trace_id_preserved(self):
        d = self._make(trace_id="deadbeef").to_dict()
        assert d["trace_id"] == "deadbeef"

    def test_span_id_preserved(self):
        d = self._make(span_id="cafebabe").to_dict()
        assert d["span_id"] == "cafebabe"

    def test_timestamp_unix_nano_preserved(self):
        d = self._make(timestamp_unix_nano=12345678).to_dict()
        assert d["timestamp_unix_nano"] == 12345678

    def test_attributes_preserved(self):
        attrs = {"k": "v", "n": 42}
        d = self._make(attributes=attrs).to_dict()
        assert d["attributes"] == attrs

    def test_received_at_is_iso_string(self):
        d = self._make().to_dict()
        assert isinstance(d["received_at"], str)


class TestOTLPTraceToDict:
    def _make(self, **kwargs) -> OTLPTrace:
        defaults = dict(
            resource_attrs={"service.name": "tracer-app"},
            scope_name="my.tracer",
            trace_id="trace-abc",
            span_id="span-xyz",
            parent_span_id="",
            name="GET /api/v1/users",
            kind=2,  # SERVER
            start_time_unix_nano=1_000_000_000,
            end_time_unix_nano=1_500_000_000,
            duration_ms=500.0,
            attributes={"http.method": "GET", "http.status_code": 200},
            status_code=1,
            exporter_ip="10.0.0.3",
            received_at=_TS,
        )
        defaults.update(kwargs)
        return OTLPTrace(**defaults)

    def test_to_dict_returns_dict(self):
        assert isinstance(self._make().to_dict(), dict)

    def test_trace_id_preserved(self):
        d = self._make(trace_id="my-trace").to_dict()
        assert d["trace_id"] == "my-trace"

    def test_span_id_preserved(self):
        d = self._make(span_id="my-span").to_dict()
        assert d["span_id"] == "my-span"

    def test_duration_ms_preserved(self):
        d = self._make(duration_ms=123.456).to_dict()
        assert d["duration_ms"] == pytest.approx(123.456)

    def test_kind_preserved(self):
        d = self._make(kind=3).to_dict()
        assert d["kind"] == 3

    def test_status_code_preserved(self):
        d = self._make(status_code=2).to_dict()
        assert d["status_code"] == 2

    def test_name_preserved(self):
        d = self._make(name="my.operation").to_dict()
        assert d["name"] == "my.operation"

    def test_start_end_times_preserved(self):
        d = self._make(start_time_unix_nano=100, end_time_unix_nano=200).to_dict()
        assert d["start_time_unix_nano"] == 100
        assert d["end_time_unix_nano"] == 200

    def test_received_at_is_iso_string(self):
        d = self._make().to_dict()
        assert isinstance(d["received_at"], str)

    def test_attributes_preserved(self):
        attrs = {"db.system": "postgresql"}
        d = self._make(attributes=attrs).to_dict()
        assert d["attributes"] == attrs
