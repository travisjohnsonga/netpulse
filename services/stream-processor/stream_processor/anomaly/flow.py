"""Flow anomaly detection: high byte rate on a single flow record."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class FlowAnomaly:
    exporter_ip: str
    src_ip: str
    dst_ip: str
    mbps: float
    message: str


def detect(record: dict, threshold_mbps: float = 1000.0) -> FlowAnomaly | None:
    """
    Return a FlowAnomaly if the single-flow rate exceeds threshold_mbps,
    otherwise None.

    bytes / duration_s * 8 / 1_000_000 = Mbps
    """
    duration_s = max(record.get("duration_ms", 1000) / 1000.0, 0.001)
    bps = record.get("bytes", 0) / duration_s
    mbps = bps * 8 / 1_000_000
    if mbps <= threshold_mbps:
        return None
    exporter = record.get("exporter_ip", "")
    src = record.get("src_ip", "")
    dst = record.get("dst_ip", "")
    return FlowAnomaly(
        exporter_ip=exporter,
        src_ip=src,
        dst_ip=dst,
        mbps=mbps,
        message=f"Flow rate {mbps:.0f} Mbps from {src} to {dst} via {exporter}",
    )
