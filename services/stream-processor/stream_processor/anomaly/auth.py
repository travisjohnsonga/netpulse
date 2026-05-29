"""
Auth event anomaly detection from syslog and OTLP log streams.

Two attack patterns:
  - Brute force: ≥ BRUTE_FORCE_COUNT failures from same source IP
                 within BRUTE_FORCE_WINDOW_S seconds. → severity: high
  - Slow burn:   ≥ SLOW_BURN_COUNT failures from same source IP
                 within SLOW_BURN_WINDOW_S seconds (no brute-force
                 threshold hit). → severity: medium
"""
from __future__ import annotations

import re
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Deque

# Patterns that indicate a failed authentication attempt.
_AUTH_FAIL_RE = re.compile(
    r"(authentication fail|failed password|invalid user|"
    r"failed login|login fail|bad password|incorrect password|"
    r"access denied|unauthorized|invalid credentials)",
    re.I,
)

# Patterns used to extract the source IP from the log message.
_SRC_IP_RE = re.compile(
    r"from\s+(\d{1,3}(?:\.\d{1,3}){3})"
    r"|src=(\d{1,3}(?:\.\d{1,3}){3})"
    r"|client\s+(\d{1,3}(?:\.\d{1,3}){3})"
    r"|\[(\d{1,3}(?:\.\d{1,3}){3})\]",
    re.I,
)

BRUTE_FORCE_COUNT    = 5
BRUTE_FORCE_WINDOW_S = 60.0
SLOW_BURN_COUNT      = 15
SLOW_BURN_WINDOW_S   = 3600.0


@dataclass
class AuthAnomaly:
    src_ip: str
    device_ip: str
    count: int
    window_s: float
    attack_type: str   # "brute_force" | "slow_burn"
    severity: str      # "high" | "medium"
    message: str


def _extract_src_ip(text: str) -> str:
    m = _SRC_IP_RE.search(text)
    if not m:
        return ""
    return next(g for g in m.groups() if g)


@dataclass
class _SourceState:
    timestamps: Deque[float] = field(default_factory=deque)

    def record(self, ts: float) -> None:
        self.timestamps.append(ts)

    def count_in_window(self, window_s: float, now: float) -> int:
        cutoff = now - window_s
        while self.timestamps and self.timestamps[0] < cutoff:
            self.timestamps.popleft()
        return len(self.timestamps)


class AuthAnomalyDetector:
    """
    Stateful detector. One instance per stream-processor process.
    Thread-safety: single async event loop only — no locking needed.
    """

    def __init__(
        self,
        brute_force_count: int = BRUTE_FORCE_COUNT,
        brute_force_window_s: float = BRUTE_FORCE_WINDOW_S,
        slow_burn_count: int = SLOW_BURN_COUNT,
        slow_burn_window_s: float = SLOW_BURN_WINDOW_S,
    ) -> None:
        self._bf_count = brute_force_count
        self._bf_window = brute_force_window_s
        self._sb_count = slow_burn_count
        self._sb_window = slow_burn_window_s
        # key: (device_ip, src_ip)
        self._state: dict[tuple[str, str], _SourceState] = {}
        # Track which (device, src) combos have already fired each type
        # to avoid repeated alerts until the window rolls over.
        self._alerted_bf: set[tuple[str, str]] = set()
        self._alerted_sb: set[tuple[str, str]] = set()

    def feed(self, record: dict) -> AuthAnomaly | None:
        """
        Feed a log/syslog record. Return an AuthAnomaly if a threshold is
        breached for the first time in the current window, else None.
        """
        body = record.get("body", "") or record.get("message", "") or record.get("raw", "")
        if not _AUTH_FAIL_RE.search(body):
            return None

        src_ip    = _extract_src_ip(body) or record.get("source_ip", "unknown")
        device_ip = record.get("exporter_ip", record.get("source_ip", "unknown"))
        key       = (device_ip, src_ip)
        now       = time.monotonic()

        state = self._state.setdefault(key, _SourceState())
        state.record(now)

        bf_n = state.count_in_window(self._bf_window, now)
        sb_n = state.count_in_window(self._sb_window, now)

        if bf_n >= self._bf_count and key not in self._alerted_bf:
            self._alerted_bf.add(key)
            return AuthAnomaly(
                src_ip=src_ip, device_ip=device_ip,
                count=bf_n, window_s=self._bf_window,
                attack_type="brute_force", severity="high",
                message=(
                    f"Brute-force: {bf_n} auth failures from {src_ip} "
                    f"to {device_ip} in {self._bf_window:.0f}s"
                ),
            )

        if sb_n >= self._sb_count and key not in self._alerted_sb:
            self._alerted_sb.add(key)
            return AuthAnomaly(
                src_ip=src_ip, device_ip=device_ip,
                count=sb_n, window_s=self._sb_window,
                attack_type="slow_burn", severity="medium",
                message=(
                    f"Slow-burn: {sb_n} auth failures from {src_ip} "
                    f"to {device_ip} in {self._sb_window:.0f}s"
                ),
            )

        return None

    def evict_stale(self) -> None:
        """
        Remove entries whose slow-burn window has fully expired.
        Also resets alert flags for those keys.
        """
        now = time.monotonic()
        stale = [
            k for k, s in self._state.items()
            if not s.count_in_window(self._sb_window, now)
        ]
        for k in stale:
            del self._state[k]
            self._alerted_bf.discard(k)
            self._alerted_sb.discard(k)
