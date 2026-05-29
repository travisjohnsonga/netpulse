"""
Inter-device transit-latency correlator.

Algorithm
---------
When the same IP 5-tuple (src_ip, dst_ip, src_port, dst_port, ip_proto) is
observed at two *different* exporters within `window` seconds, the correlator
infers hop-by-hop WAN latency: the exporter with the earlier abs_start_time is
upstream, the later one is downstream.

    latency = downstream.abs_start_time - upstream.abs_start_time

Results are emitted as LatencyObservation instances.

State management
----------------
Pending observations are stored in a dict keyed by 5-tuple.  Each entry holds
up to `max_per_key` recent FlowRecord snapshots (rolling window).  On each new
record, the engine tries to match against stored records from *different*
exporters.  Entries older than `window` seconds are evicted lazily.

Thread safety: all methods are called from the asyncio event loop — no locking.
"""
from __future__ import annotations

import time
from collections import defaultdict
from datetime import datetime, timezone

from .models import FlowRecord, LatencyObservation

# Type alias
_FiveTuple = tuple[str, str, int, int, int]


class FlowCorrelator:
    def __init__(self, window: float = 30.0, max_per_key: int = 20) -> None:
        self._window     = window
        self._max        = max_per_key
        # 5-tuple → list of (wall_clock_received, FlowRecord)
        self._pending: defaultdict[_FiveTuple, list[tuple[float, FlowRecord]]] = defaultdict(list)

    def feed(self, record: FlowRecord) -> list[LatencyObservation]:
        """
        Submit a decoded flow record.  Returns any LatencyObservation instances
        that can be inferred from this record paired with previously seen records
        from different exporters.
        """
        key = record.five_tuple()
        now = time.monotonic()
        cutoff = now - self._window

        # Evict stale entries for this key
        bucket = self._pending[key]
        bucket[:] = [(t, r) for t, r in bucket if t >= cutoff]

        # Try to match against stored records from different exporters
        observations: list[LatencyObservation] = []
        for _, stored in bucket:
            if stored.exporter_ip == record.exporter_ip:
                continue
            obs = self._compute(stored, record)
            if obs:
                observations.append(obs)

        # Store this record (cap at max_per_key)
        bucket.append((now, record))
        if len(bucket) > self._max:
            bucket.pop(0)

        return observations

    def _compute(self, a: FlowRecord, b: FlowRecord) -> LatencyObservation | None:
        """
        Determine which record is upstream (earlier start time) and produce a
        LatencyObservation.  Returns None if the delta is implausible.
        """
        if a.abs_start_time <= b.abs_start_time:
            upstream, downstream = a, b
        else:
            upstream, downstream = b, a

        latency_ms = (downstream.abs_start_time - upstream.abs_start_time) * 1000.0

        # Sanity bounds: 0 ms ≤ latency ≤ window * 1000 ms
        if latency_ms < 0 or latency_ms > self._window * 1000:
            return None

        return LatencyObservation(
            src_device=upstream.exporter_ip,
            dst_device=downstream.exporter_ip,
            src_ip=upstream.src_ip,
            dst_ip=upstream.dst_ip,
            src_port=upstream.src_port,
            dst_port=upstream.dst_port,
            ip_protocol=upstream.ip_protocol,
            latency_ms=latency_ms,
            observed_at=datetime.now(timezone.utc),
        )

    def evict_all_stale(self) -> None:
        """
        Sweep the entire pending dict and drop entries older than window.
        Call periodically (e.g., every `window` seconds) to reclaim memory.
        """
        now = time.monotonic()
        cutoff = now - self._window
        empty_keys = [k for k, v in self._pending.items()
                      if all(t < cutoff for t, _ in v)]
        for k in empty_keys:
            del self._pending[k]
        # Also trim live buckets
        for bucket in self._pending.values():
            bucket[:] = [(t, r) for t, r in bucket if t >= cutoff]
