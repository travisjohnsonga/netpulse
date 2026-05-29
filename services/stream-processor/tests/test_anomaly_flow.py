"""Tests for flow rate anomaly detection."""
from stream_processor.anomaly.flow import FlowAnomaly, detect


class TestFlowAnomalyDetect:
    def test_below_threshold_returns_none(self):
        record = {"bytes": 1_000, "duration_ms": 1000, "src_ip": "1.1.1.1", "dst_ip": "2.2.2.2"}
        assert detect(record, threshold_mbps=1000.0) is None

    def test_at_threshold_returns_none(self):
        # exactly 1000 Mbps — not strictly above
        bits = 1000 * 1_000_000
        record = {"bytes": bits // 8, "duration_ms": 1000}
        assert detect(record, threshold_mbps=1000.0) is None

    def test_above_threshold_returns_anomaly(self):
        bits = 1001 * 1_000_000
        record = {
            "bytes": bits // 8,
            "duration_ms": 1000,
            "exporter_ip": "10.0.0.1",
            "src_ip": "10.1.0.1",
            "dst_ip": "10.2.0.1",
        }
        result = detect(record, threshold_mbps=1000.0)
        assert isinstance(result, FlowAnomaly)
        assert result.mbps > 1000.0

    def test_anomaly_fields(self):
        record = {
            "bytes": 2_000_000,
            "duration_ms": 1,  # very short → huge rate
            "exporter_ip": "172.16.0.1",
            "src_ip": "192.168.1.1",
            "dst_ip": "8.8.8.8",
        }
        result = detect(record, threshold_mbps=100.0)
        assert result is not None
        assert result.exporter_ip == "172.16.0.1"
        assert result.src_ip == "192.168.1.1"
        assert result.dst_ip == "8.8.8.8"
        assert "192.168.1.1" in result.message

    def test_zero_bytes_returns_none(self):
        record = {"bytes": 0, "duration_ms": 1000}
        assert detect(record, threshold_mbps=0.1) is None

    def test_zero_duration_uses_minimum_floor(self):
        # duration_ms=0 should not divide-by-zero
        record = {"bytes": 10_000_000, "duration_ms": 0}
        result = detect(record, threshold_mbps=0.1)
        assert result is not None  # huge rate due to near-zero duration

    def test_missing_fields_default_gracefully(self):
        result = detect({}, threshold_mbps=0.001)
        assert result is None  # 0 bytes → 0 Mbps → no anomaly

    def test_custom_threshold(self):
        record = {"bytes": 125_000, "duration_ms": 1000}  # 1 Mbps
        assert detect(record, threshold_mbps=2.0) is None
        assert detect(record, threshold_mbps=0.5) is not None
