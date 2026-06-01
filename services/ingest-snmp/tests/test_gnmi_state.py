"""Tests for ingest.gnmi_state.GNMIActivity (adaptive-polling heartbeat read).

Async coroutines are driven with asyncio.run() so no pytest-asyncio plugin is
required (the existing suite is plain pytest).
"""
import asyncio
import datetime as dt

from ingest.gnmi_state import GNMIActivity


def _iso(seconds_ago: float) -> str:
    return (dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=seconds_ago)).isoformat()


class FakeRedis:
    """Minimal async redis stub: get() returns a preset value or raises."""

    def __init__(self, value=None, raises=False):
        self._value = value
        self._raises = raises
        self.gets = 0

    async def get(self, key):
        self.gets += 1
        if self._raises:
            raise ConnectionError("valkey down")
        return self._value


def _activity(fake, threshold=120):
    a = GNMIActivity(url="redis://x", threshold_seconds=threshold)
    a._client = fake  # inject, bypass lazy redis import
    return a


def test_active_when_recent():
    a = _activity(FakeRedis(value=_iso(15).encode()))
    assert asyncio.run(a.is_active("3")) is True


def test_inactive_when_stale():
    # Older than the 120s threshold → not active (SNMP should resume).
    a = _activity(FakeRedis(value=_iso(200).encode()))
    assert asyncio.run(a.is_active("3")) is False


def test_inactive_when_key_missing():
    a = _activity(FakeRedis(value=None))
    assert asyncio.run(a.is_active("3")) is False


def test_accepts_str_value():
    # redis decode_responses=True would hand back a str rather than bytes.
    a = _activity(FakeRedis(value=_iso(5)))
    assert asyncio.run(a.is_active("3")) is True


def test_malformed_value_is_inactive():
    a = _activity(FakeRedis(value=b"not-a-timestamp"))
    assert asyncio.run(a.is_active("3")) is False


def test_valkey_unavailable_degrades_to_inactive():
    # Graceful degradation: Valkey errors → False (poll via SNMP), warn once.
    a = _activity(FakeRedis(raises=True))
    assert asyncio.run(a.is_active("3")) is False
    assert a._warned is True


def test_threshold_boundary_respected():
    a = _activity(FakeRedis(value=_iso(50)), threshold=30)
    assert asyncio.run(a.is_active("3")) is False
