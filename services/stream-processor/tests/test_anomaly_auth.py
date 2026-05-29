"""Tests for authentication event anomaly detection."""
import time
from unittest.mock import patch

import pytest
from stream_processor.anomaly.auth import (
    AuthAnomalyDetector,
    AuthAnomaly,
    BRUTE_FORCE_COUNT,
    BRUTE_FORCE_WINDOW_S,
    SLOW_BURN_COUNT,
    SLOW_BURN_WINDOW_S,
    _extract_src_ip,
)


# ── IP extraction ─────────────────────────────────────────────────────────────

class TestExtractSrcIP:
    def test_from_pattern(self):
        assert _extract_src_ip("authentication failure from 192.168.1.100") == "192.168.1.100"

    def test_src_equals_pattern(self):
        assert _extract_src_ip("failed login src=10.0.0.5") == "10.0.0.5"

    def test_client_pattern(self):
        assert _extract_src_ip("refused connection from client 172.16.0.99") == "172.16.0.99"

    def test_bracketed_ip(self):
        assert _extract_src_ip("sshd: invalid user from [203.0.113.5]") == "203.0.113.5"

    def test_no_ip_returns_empty(self):
        assert _extract_src_ip("generic log message with no IP") == ""

    def test_multiple_ips_takes_first_match(self):
        result = _extract_src_ip("failed password from 10.0.0.1 to 10.0.0.2")
        assert result == "10.0.0.1"


# ── Auth message detection ────────────────────────────────────────────────────

class TestAuthMessagePatterns:
    def _det(self):
        return AuthAnomalyDetector(
            brute_force_count=3,
            brute_force_window_s=60.0,
            slow_burn_count=5,
            slow_burn_window_s=3600.0,
        )

    def test_no_auth_pattern_returns_none(self):
        det = self._det()
        assert det.feed({"body": "interface GigabitEthernet0 is up"}) is None

    def test_authentication_failure_pattern(self):
        det = self._det()
        record = {"body": "authentication failure from 10.0.0.1", "exporter_ip": "10.1.0.1"}
        # Feed below threshold — no anomaly yet
        det.feed(record)
        det.feed(record)
        result = det.feed(record)
        assert isinstance(result, AuthAnomaly)

    def test_failed_password_pattern(self):
        det = self._det()
        record = {"body": "Failed password for user from 192.168.1.5", "exporter_ip": "10.1.0.1"}
        for _ in range(2):
            det.feed(record)
        result = det.feed(record)
        assert result is not None

    def test_invalid_user_pattern(self):
        det = self._det()
        record = {"body": "Invalid user admin from 10.2.0.1", "exporter_ip": "10.1.0.1"}
        for _ in range(2):
            det.feed(record)
        result = det.feed(record)
        assert result is not None


# ── Brute force detection ─────────────────────────────────────────────────────

class TestBruteForce:
    def _det(self, bf_count=3, bf_window=60.0, sb_count=10, sb_window=3600.0):
        return AuthAnomalyDetector(
            brute_force_count=bf_count,
            brute_force_window_s=bf_window,
            slow_burn_count=sb_count,
            slow_burn_window_s=sb_window,
        )

    def _record(self, src="10.0.0.99", device="10.1.0.1"):
        return {
            "body": f"authentication failure from {src}",
            "exporter_ip": device,
        }

    def test_below_threshold_no_alert(self):
        det = self._det()
        r = self._record()
        for _ in range(2):
            assert det.feed(r) is None

    def test_at_threshold_fires(self):
        det = self._det(bf_count=3)
        r = self._record()
        det.feed(r)
        det.feed(r)
        result = det.feed(r)
        assert result is not None
        assert result.attack_type == "brute_force"
        assert result.severity == "high"

    def test_fires_only_once(self):
        det = self._det(bf_count=3)
        r = self._record()
        results = [det.feed(r) for _ in range(10)]
        fires = [r for r in results if r is not None and r.attack_type == "brute_force"]
        assert len(fires) == 1

    def test_different_src_ips_tracked_independently(self):
        det = self._det(bf_count=3)
        r1 = self._record(src="10.0.0.1")
        r2 = self._record(src="10.0.0.2")
        for _ in range(3):
            det.feed(r1)
        result1 = [det.feed(r2) for _ in range(3)]
        fires = [r for r in result1 if r and r.attack_type == "brute_force"]
        assert len(fires) == 1

    def test_different_device_ips_tracked_independently(self):
        det = self._det(bf_count=3)
        r1 = self._record(device="10.1.0.1")
        r2 = self._record(device="10.1.0.2")
        for _ in range(3):
            det.feed(r1)
        results = [det.feed(r2) for _ in range(3)]
        fires = [r for r in results if r and r.attack_type == "brute_force"]
        assert len(fires) == 1

    def test_anomaly_fields(self):
        det = self._det(bf_count=3)
        r = self._record(src="1.2.3.4", device="5.6.7.8")
        det.feed(r)
        det.feed(r)
        result = det.feed(r)
        assert result.src_ip == "1.2.3.4"
        assert result.device_ip == "5.6.7.8"
        assert result.count == 3
        assert "1.2.3.4" in result.message
        assert "5.6.7.8" in result.message

    def test_expired_events_not_counted(self):
        det = self._det(bf_count=3, bf_window=1.0)
        r = self._record()
        det.feed(r)
        det.feed(r)
        # Simulate time passing past the window
        with patch("stream_processor.anomaly.auth.time.monotonic", return_value=time.monotonic() + 2.0):
            # Feed one more — but the old ones are outside the 1s window
            result = det.feed(r)
        assert result is None  # only 1 event in window now


# ── Slow burn detection ───────────────────────────────────────────────────────

class TestSlowBurn:
    def _det(self, bf_count=100, bf_window=60.0, sb_count=5, sb_window=3600.0):
        # High BF threshold so slow burn fires first
        return AuthAnomalyDetector(
            brute_force_count=bf_count,
            brute_force_window_s=bf_window,
            slow_burn_count=sb_count,
            slow_burn_window_s=sb_window,
        )

    def _record(self):
        return {"body": "authentication failure from 10.0.0.55", "exporter_ip": "10.1.0.1"}

    def test_slow_burn_fires_at_threshold(self):
        det = self._det(sb_count=5)
        r = self._record()
        for _ in range(4):
            assert det.feed(r) is None
        result = det.feed(r)
        assert result is not None
        assert result.attack_type == "slow_burn"
        assert result.severity == "medium"

    def test_slow_burn_fires_only_once(self):
        det = self._det(sb_count=5)
        r = self._record()
        results = [det.feed(r) for _ in range(20)]
        fires = [x for x in results if x and x.attack_type == "slow_burn"]
        assert len(fires) == 1


# ── Stale eviction ────────────────────────────────────────────────────────────

class TestEvictStale:
    def test_evict_removes_old_keys(self):
        det = AuthAnomalyDetector(slow_burn_window_s=0.001)
        r = {"body": "authentication failure from 10.0.0.1", "exporter_ip": "10.1.0.1"}
        det.feed(r)
        assert len(det._state) == 1
        import time as _time; _time.sleep(0.01)
        det.evict_stale()
        assert len(det._state) == 0

    def test_evict_resets_alert_flags(self):
        det = AuthAnomalyDetector(
            brute_force_count=3, brute_force_window_s=0.001,
            slow_burn_count=5,   slow_burn_window_s=0.001,
        )
        r = {"body": "authentication failure from 10.0.0.1", "exporter_ip": "10.1.0.1"}
        for _ in range(3):
            det.feed(r)
        import time as _time; _time.sleep(0.02)
        det.evict_stale()
        assert len(det._alerted_bf) == 0
        assert len(det._alerted_sb) == 0
