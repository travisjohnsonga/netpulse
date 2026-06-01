"""Tests for ingest.gnmi_heartbeat.GNMIHeartbeat (writes the Valkey heartbeat).

Coroutines are driven with asyncio.run() (no pytest-asyncio dependency).
"""
import asyncio

from ingest.gnmi_heartbeat import GNMIHeartbeat


class FakeRedis:
    def __init__(self, raises=False):
        self._raises = raises
        self.sets = []

    async def set(self, key, value, ex=None):
        if self._raises:
            raise ConnectionError("valkey down")
        self.sets.append((key, value, ex))


def _hb(fake, ttl=180):
    hb = GNMIHeartbeat(url="redis://x", ttl=ttl)
    hb._client = fake  # inject, bypass lazy redis import
    return hb


def test_mark_active_sets_key_with_ttl():
    fake = FakeRedis()
    hb = _hb(fake, ttl=180)
    asyncio.run(hb.mark_active("42"))
    assert len(fake.sets) == 1
    key, value, ex = fake.sets[0]
    assert key == "gnmi:last_seen:42"
    assert ex == 180
    assert "T" in value  # ISO-8601 timestamp


def test_mark_active_degrades_gracefully():
    # Valkey down → no raise, warns once, telemetry ingest continues.
    fake = FakeRedis(raises=True)
    hb = _hb(fake)
    asyncio.run(hb.mark_active("42"))  # must not raise
    assert hb._warned is True
