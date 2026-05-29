"""
Alert deduplication: suppress re-publishing the same (device, condition)
pair within a configurable cooldown window.
"""
from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass


@dataclass
class Alert:
    severity: str     # "high" | "medium" | "low"
    condition: str    # machine-readable condition name
    device_id: str
    message: str
    extra: dict


class AlertDeduplicator:
    def __init__(self, cooldown_s: float = 300.0) -> None:
        self._cooldown = cooldown_s
        self._last_seen: dict[tuple[str, str], float] = defaultdict(float)

    def should_publish(self, alert: Alert) -> bool:
        """Return True if the alert is not within the cooldown window."""
        key = (alert.device_id, alert.condition)
        now = time.monotonic()
        if now - self._last_seen[key] < self._cooldown:
            return False
        self._last_seen[key] = now
        return True

    def reset(self, device_id: str = "", condition: str = "") -> None:
        """Force-reset a specific key (useful in tests)."""
        key = (device_id, condition)
        if key in self._last_seen:
            del self._last_seen[key]
