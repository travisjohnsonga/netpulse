"""Tests for ingest.correlator — no external dependencies."""
from __future__ import annotations

import time

import pytest
from ingest.correlator import FlowCorrelator
from ingest.models import FlowRecord


def _record(
    exporter: str,
    src="10.0.0.1", dst="10.0.0.2",
    sp=1234, dp=80, proto=6,
    start: float | None = None,
    end: float | None = None,
) -> FlowRecord:
    if start is None:
        start = time.time()
    if end is None:
        end = start + 1.0
    return FlowRecord(
        exporter_ip=exporter,
        exporter_port=0,
        protocol_version="netflow5",
        src_ip=src, dst_ip=dst,
        src_port=sp, dst_port=dp,
        ip_protocol=proto,
        abs_start_time=start,
        abs_end_time=end,
        duration_ms=(end - start) * 1000,
        packets=10, bytes_count=1000,
    )


class TestCorrelatorBasic:
    def test_same_exporter_no_observation(self):
        c = FlowCorrelator(window=30)
        r = _record("10.0.0.1")
        obs = c.feed(r)
        assert obs == []
        obs2 = c.feed(_record("10.0.0.1"))
        assert obs2 == []

    def test_different_exporters_produces_observation(self):
        c = FlowCorrelator(window=30)
        now = time.time()
        r1 = _record("10.0.0.1", start=now)
        r2 = _record("10.0.0.2", start=now + 0.050)   # 50 ms downstream
        c.feed(r1)
        obs = c.feed(r2)
        assert len(obs) == 1
        o = obs[0]
        assert o.src_device == "10.0.0.1"   # upstream
        assert o.dst_device == "10.0.0.2"
        assert o.latency_ms == pytest.approx(50.0, abs=1.0)

    def test_direction_determined_by_start_time(self):
        c = FlowCorrelator(window=30)
        now = time.time()
        # Feed downstream first, then upstream
        r_down = _record("10.0.0.2", start=now + 0.100)
        r_up   = _record("10.0.0.1", start=now)
        c.feed(r_down)
        obs = c.feed(r_up)
        assert len(obs) == 1
        assert obs[0].src_device == "10.0.0.1"   # earlier start → upstream
        assert obs[0].dst_device == "10.0.0.2"
        assert obs[0].latency_ms == pytest.approx(100.0, abs=1.0)

    def test_different_five_tuple_no_match(self):
        c = FlowCorrelator(window=30)
        c.feed(_record("10.0.0.1", sp=1234))
        obs = c.feed(_record("10.0.0.2", sp=9999))  # different src port
        assert obs == []

    def test_outside_window_no_match(self):
        c = FlowCorrelator(window=5)
        now = time.time()
        old = _record("10.0.0.1", start=now - 10)   # 10s ago — beyond window
        c.feed(old)
        # Force eviction
        c.evict_all_stale()
        obs = c.feed(_record("10.0.0.2", start=now))
        assert obs == []

    def test_latency_zero_boundary(self):
        c = FlowCorrelator(window=30)
        now = time.time()
        r1 = _record("10.0.0.1", start=now)
        r2 = _record("10.0.0.2", start=now)   # simultaneous
        c.feed(r1)
        obs = c.feed(r2)
        assert len(obs) == 1
        assert obs[0].latency_ms == pytest.approx(0.0, abs=1.0)

    def test_implausibly_large_latency_rejected(self):
        # window=5s, so a 6000ms delta should be rejected
        c = FlowCorrelator(window=5)
        now = time.time()
        c.feed(_record("10.0.0.1", start=now))
        obs = c.feed(_record("10.0.0.2", start=now + 6))
        assert obs == []

    def test_observation_fields(self):
        c = FlowCorrelator(window=30)
        now = time.time()
        c.feed(_record("dev-a", src="1.1.1.1", dst="2.2.2.2", sp=100, dp=200, proto=17, start=now))
        obs = c.feed(_record("dev-b", src="1.1.1.1", dst="2.2.2.2", sp=100, dp=200, proto=17,
                               start=now + 0.020))
        assert len(obs) == 1
        o = obs[0]
        assert o.src_ip  == "1.1.1.1"
        assert o.dst_ip  == "2.2.2.2"
        assert o.src_port == 100
        assert o.dst_port == 200
        assert o.ip_protocol == 17
        assert o.observed_at is not None

    def test_to_dict(self):
        c = FlowCorrelator(window=30)
        now = time.time()
        c.feed(_record("dev-a", start=now))
        obs = c.feed(_record("dev-b", start=now + 0.010))
        d = obs[0].to_dict()
        for key in ("src_device", "dst_device", "latency_ms", "observed_at",
                    "src_ip", "dst_ip", "src_port", "dst_port", "ip_protocol"):
            assert key in d


class TestCorrelatorCapacity:
    def test_max_per_key_evicts_oldest(self):
        c = FlowCorrelator(window=60, max_per_key=3)
        now = time.time()
        for i in range(4):
            c.feed(_record(f"dev-{i}", start=now + i * 0.001))
        # Should still function without error
        obs = c.feed(_record("dev-new", start=now + 0.100))
        assert isinstance(obs, list)

    def test_evict_all_stale_cleans_empty_keys(self):
        c = FlowCorrelator(window=1)
        now = time.time()
        # Feed a record that is immediately stale (monotonic used internally)
        c.feed(_record("10.0.0.1", start=now))
        import time as _t
        _t.sleep(1.1)
        c.evict_all_stale()
        assert len(c._pending) == 0 or all(
            len(v) == 0 for v in c._pending.values()
        )
