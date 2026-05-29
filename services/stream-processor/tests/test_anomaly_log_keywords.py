"""Tests for log keyword anomaly detection."""
from stream_processor.anomaly.log_keywords import LogAnomaly, detect


class TestLogKeywordDetect:
    def test_no_keyword_returns_none(self):
        assert detect({"body": "interface GigabitEthernet0/0 is up"}) is None

    def test_error_keyword(self):
        result = detect({"body": "BGP session error with peer 10.0.0.1"})
        assert isinstance(result, LogAnomaly)
        assert result.matched_keyword == "error"

    def test_critical_keyword(self):
        result = detect({"body": "CRITICAL: disk full on /var"})
        assert result is not None
        assert result.matched_keyword == "critical"

    def test_down_keyword(self):
        result = detect({"body": "Interface FastEthernet1/0 is down"})
        assert result is not None
        assert result.matched_keyword == "down"

    def test_unreachable_keyword(self):
        result = detect({"body": "host 10.0.0.5 is unreachable"})
        assert result is not None
        assert result.matched_keyword == "unreachable"

    def test_failed_keyword(self):
        result = detect({"body": "Failed to establish BGP session"})
        assert result is not None
        assert result.matched_keyword == "failed"

    def test_failure_keyword(self):
        result = detect({"body": "hardware failure detected on linecard 2"})
        assert result is not None
        assert result.matched_keyword == "failure"

    def test_case_insensitive(self):
        assert detect({"body": "ERROR: timeout"}) is not None
        assert detect({"body": "DOWN: link state change"}) is not None

    def test_exporter_ip_captured(self):
        result = detect({"body": "link error", "exporter_ip": "10.1.1.1"})
        assert result is not None
        assert result.exporter_ip == "10.1.1.1"

    def test_source_ip_fallback(self):
        result = detect({"body": "link error", "source_ip": "10.2.2.2"})
        assert result is not None
        assert result.exporter_ip == "10.2.2.2"

    def test_message_field_also_checked(self):
        result = detect({"message": "kernel error in module X"})
        assert result is not None

    def test_partial_word_not_matched(self):
        # "download" contains "down" but shouldn't match with word-boundary
        result = detect({"body": "download complete successfully"})
        assert result is None

    def test_empty_body_returns_none(self):
        assert detect({}) is None
        assert detect({"body": ""}) is None

    def test_body_truncated_to_500(self):
        long_body = "error " + "x" * 1000
        result = detect({"body": long_body})
        assert result is not None
        assert len(result.body) <= 500
