"""
Counter-delta → rate conversion for interface traffic metrics.

Raw SNMP/gNMI interface counters (ifHCInOctets, in-octets, …) are monotonic
byte/packet totals. To show actual throughput we keep the previous sample per
(device, counter) and divide the delta by the elapsed wall-clock time, yielding
bits/sec (octets ×8) and packets/sec.

Two failure modes are guarded:
  * counter reset (device reboot / SNMP agent restart) → delta goes negative →
    skipped, so no negative or absurd spike is emitted.
  * the very first sample for a counter has nothing to diff against → no rate
    emitted until the second sample.

State is in-memory and keyed by (device_id, metric_name); interface counts are
bounded so it does not grow without limit. The poll interval is far longer than
any practical restart cadence, so we err on the side of dropping a sample rather
than reporting a wrong one.
"""
from __future__ import annotations

import re


def _normalize(leaf: str) -> str:
    """Lowercase a counter leaf name and strip non-alphanumerics for matching."""
    return re.sub(r"[^a-z0-9]", "", leaf.lower())


def classify_counter(leaf: str) -> tuple[str, float] | None:
    """Map a raw counter leaf name → (rate suffix, multiplier), or None.

    Recognises both SNMP (``ifHCInOctets``) and gNMI/OpenConfig (``in-octets``)
    spellings. Octet counters convert bytes→bits (×8) and become *_in_bps /
    *_out_bps; packet counters become *_in_pps / *_out_pps. Error/discard
    counters are intentionally not rated here.
    """
    s = _normalize(leaf)
    if "octet" in s:
        unit, mult = "bps", 8.0
    elif "pkt" in s or "packet" in s:
        unit, mult = "pps", 1.0
    else:
        return None
    # Direction: check "out" before "in" ("in" never appears in "...octets").
    if "out" in s:
        direction = "out"
    elif "in" in s:
        direction = "in"
    else:
        return None
    return f"{direction}_{unit}", mult


def _split(name: str) -> tuple[str, str]:
    """Split a metric name into (interface_label, counter_leaf).

    gNMI flattened names look like ``GigabitEthernet1/in-octets`` (iface/leaf).
    SNMP names look like ``ifHCInOctets.5`` (leaf.instance), where the instance
    index identifies the interface.
    """
    if "/" in name:
        iface, _, leaf = name.rpartition("/")
        return iface, leaf
    if "." in name:
        leaf, _, instance = name.partition(".")
        return instance, leaf
    return "", name


def _field_label(iface: str) -> str:
    """Normalize an interface label for use as an InfluxDB field key."""
    return iface.replace(".", "_").replace("-", "_")


class RateCalculator:
    """Stateful per-(device, counter) delta→rate converter."""

    def __init__(self) -> None:
        # (device_id, metric_name) → (value, timestamp_seconds)
        self._state: dict[tuple[str, str], tuple[float, float]] = {}

    def compute(self, device_id: str, nested_metrics: dict, ts: float) -> dict[str, float]:
        """Return derived {field: rate} for interface counters in this sample.

        ``nested_metrics`` is the SNMP/gNMI ``metrics`` dict (oid/name → entry
        with ``name``/``value``). ``ts`` is the sample's wall-clock time in
        seconds. Counters seen for the first time, counter resets, and
        non-positive time deltas produce no output.
        """
        derived: dict[str, float] = {}
        for entry in nested_metrics.values():
            if not isinstance(entry, dict):
                continue
            name = entry.get("name")
            if not name:
                continue
            iface, leaf = _split(name)
            kind = classify_counter(leaf)
            if kind is None:
                continue
            suffix, mult = kind

            value = entry.get("value")
            if not isinstance(value, (int, float)):
                try:
                    value = float(value)
                except (TypeError, ValueError):
                    continue

            key = (device_id, name)
            prev = self._state.get(key)
            self._state[key] = (float(value), ts)
            if prev is None:
                continue
            prev_value, prev_ts = prev
            dt = ts - prev_ts
            if dt <= 0:
                continue
            delta = float(value) - prev_value
            if delta < 0:
                # Counter reset / wrap — skip rather than emit a false spike.
                continue
            field = f"{_field_label(iface)}_{suffix}" if iface else suffix
            derived[field] = delta * mult / dt
        return derived
