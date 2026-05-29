"""Tests for alert deduplication."""
import time
from unittest.mock import patch

from stream_processor.alert_dedup import Alert, AlertDeduplicator


def _alert(condition="high_flow_rate", device_id="10.0.0.1"):
    return Alert(
        severity="high", condition=condition,
        device_id=device_id, message="test", extra={},
    )


class TestAlertDeduplicator:
    def test_first_alert_passes(self):
        dedup = AlertDeduplicator(cooldown_s=300.0)
        assert dedup.should_publish(_alert()) is True

    def test_second_alert_suppressed_within_cooldown(self):
        dedup = AlertDeduplicator(cooldown_s=300.0)
        dedup.should_publish(_alert())
        assert dedup.should_publish(_alert()) is False

    def test_alert_allowed_after_cooldown(self):
        dedup = AlertDeduplicator(cooldown_s=1.0)
        dedup.should_publish(_alert())
        # Mock time advancing past cooldown
        future = time.monotonic() + 2.0
        with patch("stream_processor.alert_dedup.time.monotonic", return_value=future):
            assert dedup.should_publish(_alert()) is True

    def test_different_conditions_independent(self):
        dedup = AlertDeduplicator(cooldown_s=300.0)
        assert dedup.should_publish(_alert(condition="high_flow_rate")) is True
        assert dedup.should_publish(_alert(condition="high_latency")) is True

    def test_different_devices_independent(self):
        dedup = AlertDeduplicator(cooldown_s=300.0)
        assert dedup.should_publish(_alert(device_id="10.0.0.1")) is True
        assert dedup.should_publish(_alert(device_id="10.0.0.2")) is True

    def test_same_condition_different_devices_suppressed_separately(self):
        dedup = AlertDeduplicator(cooldown_s=300.0)
        dedup.should_publish(_alert(device_id="10.0.0.1"))
        dedup.should_publish(_alert(device_id="10.0.0.2"))
        assert dedup.should_publish(_alert(device_id="10.0.0.1")) is False
        assert dedup.should_publish(_alert(device_id="10.0.0.2")) is False

    def test_reset_allows_republish(self):
        dedup = AlertDeduplicator(cooldown_s=300.0)
        a = _alert()
        dedup.should_publish(a)
        dedup.reset(device_id=a.device_id, condition=a.condition)
        assert dedup.should_publish(a) is True

    def test_zero_cooldown_always_passes(self):
        dedup = AlertDeduplicator(cooldown_s=0.0)
        a = _alert()
        assert dedup.should_publish(a) is True
        assert dedup.should_publish(a) is True
