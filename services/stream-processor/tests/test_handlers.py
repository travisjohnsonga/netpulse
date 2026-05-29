"""
Tests for topic handlers. All external writers are mocked out.
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from stream_processor.handlers import flows as flow_handler
from stream_processor.handlers import logs as log_handler
from stream_processor.handlers import metrics as metric_handler
from stream_processor.anomaly.auth import AuthAnomalyDetector


# ── Metrics handlers ──────────────────────────────────────────────────────────

class TestHandleTelemetryMetrics:
    def test_writes_numeric_fields_to_influx(self):
        influx = MagicMock()
        data = {"cpu_util": 85.0, "mem_util": 60.0, "exporter_ip": "10.0.0.1", "vendor": "cisco"}
        metric_handler.handle_telemetry_metrics("netpulse.telemetry.rtr01.metrics", data, influx)
        influx.write.assert_called_once()
        _, _, fields = influx.write.call_args[0]
        assert "cpu_util" in fields
        assert "mem_util" in fields
        assert "exporter_ip" not in fields  # skipped
        assert "vendor" not in fields       # non-numeric

    def test_no_numeric_fields_skips_write(self):
        influx = MagicMock()
        data = {"hostname": "rtr01", "platform": "ios_xe"}
        metric_handler.handle_telemetry_metrics("netpulse.telemetry.rtr01.metrics", data, influx)
        influx.write.assert_not_called()

    def test_device_id_from_subject(self):
        influx = MagicMock()
        data = {"cpu": 50.0}
        metric_handler.handle_telemetry_metrics("netpulse.telemetry.my-router.metrics", data, influx)
        tags = influx.write.call_args[0][1]
        assert tags["device_id"] == "my-router"

    def test_none_influx_no_crash(self):
        # Should silently skip writes when influx is unavailable
        data = {"cpu": 99.9}
        metric_handler.handle_telemetry_metrics("netpulse.telemetry.rtr01.metrics", data, None)

    def test_abs_time_fields_skipped(self):
        influx = MagicMock()
        data = {"abs_start_time": 1234567, "abs_end_time": 1234568, "bytes": 500.0}
        metric_handler.handle_telemetry_metrics("netpulse.telemetry.r.metrics", data, influx)
        _, _, fields = influx.write.call_args[0]
        assert "abs_start_time" not in fields
        assert "bytes" in fields


class TestHandleOtelMetrics:
    def test_writes_gauge_data_points(self):
        influx = MagicMock()
        data = {
            "metric_name": "cpu.usage",
            "resource_attrs": {"service.name": "agent-1"},
            "data_points": [{"value": 42.5, "attributes": {"host": "srv-01"}}],
        }
        metric_handler.handle_otel_metrics("netpulse.otel.srv01.metrics", data, influx)
        influx.write.assert_called_once()
        measurement, tags, fields = influx.write.call_args[0]
        assert measurement == "otel_metrics"
        assert tags["service"] == "agent-1"
        assert tags["metric"] == "cpu.usage"
        assert fields["value"] == 42.5

    def test_non_numeric_value_skipped(self):
        influx = MagicMock()
        data = {
            "metric_name": "status",
            "resource_attrs": {},
            "data_points": [{"value": "up"}],
        }
        metric_handler.handle_otel_metrics("netpulse.otel.x.metrics", data, influx)
        influx.write.assert_not_called()

    def test_multiple_data_points_each_written(self):
        influx = MagicMock()
        data = {
            "metric_name": "mem",
            "resource_attrs": {},
            "data_points": [{"value": 10.0}, {"value": 20.0}],
        }
        metric_handler.handle_otel_metrics("x", data, influx)
        assert influx.write.call_count == 2

    def test_none_influx_no_crash(self):
        data = {"metric_name": "x", "resource_attrs": {}, "data_points": [{"value": 1.0}]}
        metric_handler.handle_otel_metrics("x", data, None)


# ── Log handlers ──────────────────────────────────────────────────────────────

class TestHandleTrap:
    def test_indexes_trap_in_opensearch(self):
        os_writer = AsyncMock()
        asyncio.run(log_handler.handle_trap("netpulse.telemetry.d.trap", {"oid": "1.3.6"}, os_writer))
        os_writer.index.assert_called_once()
        index_name = os_writer.index.call_args[0][0]
        assert index_name.startswith("netpulse-traps-")

    def test_timestamp_added(self):
        os_writer = AsyncMock()
        asyncio.run(log_handler.handle_trap("sub", {}, os_writer))
        doc = os_writer.index.call_args[0][1]
        assert "@timestamp" in doc

    def test_none_os_writer_no_crash(self):
        asyncio.run(log_handler.handle_trap("sub", {}, None))


class TestHandleOtelLogs:
    def test_indexes_log_in_opensearch(self):
        os_writer = AsyncMock()
        det = AuthAnomalyDetector()
        record = {"body": "BGP session established", "exporter_ip": "10.0.0.1"}
        asyncio.run(log_handler.handle_otel_logs("sub", record, os_writer, det))
        os_writer.index.assert_called_once()
        index_name = os_writer.index.call_args[0][0]
        assert index_name.startswith("netpulse-otel-logs-")

    def test_keyword_match_returns_anomaly(self):
        os_writer = AsyncMock()
        det = AuthAnomalyDetector()
        record = {"body": "critical: link down on Gi0/0", "exporter_ip": "10.0.0.1"}
        kw, auth = asyncio.run(log_handler.handle_otel_logs("sub", record, os_writer, det))
        assert kw is not None
        assert kw.matched_keyword in ("critical", "down")

    def test_no_keyword_returns_none(self):
        os_writer = AsyncMock()
        det = AuthAnomalyDetector()
        record = {"body": "BGP neighbor UP in state Established"}
        kw, auth = asyncio.run(log_handler.handle_otel_logs("sub", record, os_writer, det))
        assert kw is None

    def test_auth_hit_on_brute_force(self):
        os_writer = AsyncMock()
        det = AuthAnomalyDetector(brute_force_count=3, brute_force_window_s=60.0)
        record = {"body": "authentication failure from 1.2.3.4", "exporter_ip": "10.0.0.1"}
        kw, auth = None, None
        for _ in range(3):
            kw, auth = asyncio.run(log_handler.handle_otel_logs("sub", record.copy(), os_writer, det))
        assert auth is not None
        assert auth.attack_type == "brute_force"

    def test_none_os_writer_no_crash(self):
        det = AuthAnomalyDetector()
        asyncio.run(log_handler.handle_otel_logs("sub", {}, None, det))

    def test_none_auth_detector_no_crash(self):
        os_writer = AsyncMock()
        kw, auth = asyncio.run(log_handler.handle_otel_logs("sub", {"body": "error msg"}, os_writer, None))
        assert auth is None


class TestHandleVendor:
    def test_indexes_vendor_event(self):
        os_writer = AsyncMock()
        asyncio.run(log_handler.handle_vendor("netpulse.vendor.meraki.x", {"type": "alert"}, os_writer))
        os_writer.index.assert_called_once()
        index_name = os_writer.index.call_args[0][0]
        assert index_name.startswith("netpulse-vendor-")


# ── Flow handlers ─────────────────────────────────────────────────────────────

class TestHandleFlow:
    def test_indexes_flow_in_opensearch(self):
        os_writer = AsyncMock()
        record = {"bytes": 1000, "duration_ms": 1000, "src_ip": "1.1.1.1", "dst_ip": "2.2.2.2"}
        asyncio.run(flow_handler.handle_flow("netpulse.flows.rtr.netflow5", record, os_writer))
        os_writer.index.assert_called_once()
        index_name = os_writer.index.call_args[0][0]
        assert index_name.startswith("netpulse-flows-")

    def test_returns_anomaly_on_high_rate(self):
        os_writer = AsyncMock()
        bits = 2000 * 1_000_000  # 2000 Mbps
        record = {"bytes": bits // 8, "duration_ms": 1000}
        result = asyncio.run(flow_handler.handle_flow("sub", record, os_writer, threshold_mbps=1000.0))
        assert result is not None

    def test_returns_none_below_threshold(self):
        os_writer = AsyncMock()
        record = {"bytes": 100, "duration_ms": 1000}
        result = asyncio.run(flow_handler.handle_flow("sub", record, os_writer, threshold_mbps=1000.0))
        assert result is None

    def test_none_os_writer_no_crash(self):
        asyncio.run(flow_handler.handle_flow("sub", {}, None))


class TestHandleLatency:
    def test_writes_to_influx(self):
        influx = MagicMock()
        data = {"latency_ms": 150.0, "src_device": "rtr-a", "dst_device": "rtr-b"}
        flow_handler.handle_latency("sub", data, influx, threshold_ms=500.0)
        influx.write.assert_called_once()
        measurement = influx.write.call_args[0][0]
        assert measurement == "transit_latency"

    def test_returns_true_above_threshold(self):
        influx = MagicMock()
        data = {"latency_ms": 600.0, "src_device": "a", "dst_device": "b"}
        assert flow_handler.handle_latency("sub", data, influx, threshold_ms=500.0) is True

    def test_returns_false_below_threshold(self):
        influx = MagicMock()
        data = {"latency_ms": 100.0, "src_device": "a", "dst_device": "b"}
        assert flow_handler.handle_latency("sub", data, influx, threshold_ms=500.0) is False

    def test_none_influx_no_crash(self):
        data = {"latency_ms": 600.0, "src_device": "a", "dst_device": "b"}
        result = flow_handler.handle_latency("sub", data, None, threshold_ms=500.0)
        assert result is True  # still returns boolean correctly
