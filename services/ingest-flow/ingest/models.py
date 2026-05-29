"""
Normalised data models shared across all flow protocol decoders.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class FlowRecord:
    """
    Protocol-agnostic flow record.  All decoders produce FlowRecord instances.
    Timestamps are absolute UNIX epoch seconds (float) so the correlation engine
    can compare records across exporters directly.
    """
    exporter_ip: str
    exporter_port: int
    protocol_version: str       # "netflow5" | "netflow9" | "ipfix" | "sflow5"

    # IP 5-tuple
    src_ip: str
    dst_ip: str
    src_port: int
    dst_port: int
    ip_protocol: int            # 6=TCP 17=UDP 1=ICMP

    # Timing (absolute wall clock, seconds)
    abs_start_time: float
    abs_end_time: float
    duration_ms: float

    # Volume
    packets: int
    bytes_count: int

    # Interfaces
    input_if: int = 0
    output_if: int = 0

    # BGP / routing (optional)
    src_as: int = 0
    dst_as: int = 0

    # Layer 4
    tcp_flags: int = 0
    tos: int = 0

    def five_tuple(self) -> tuple[str, str, int, int, int]:
        return (self.src_ip, self.dst_ip, self.src_port, self.dst_port, self.ip_protocol)

    def to_dict(self) -> dict[str, Any]:
        return {
            "exporter_ip": self.exporter_ip,
            "protocol_version": self.protocol_version,
            "src_ip": self.src_ip,
            "dst_ip": self.dst_ip,
            "src_port": self.src_port,
            "dst_port": self.dst_port,
            "ip_protocol": self.ip_protocol,
            "abs_start_time": self.abs_start_time,
            "abs_end_time": self.abs_end_time,
            "duration_ms": self.duration_ms,
            "packets": self.packets,
            "bytes": self.bytes_count,
            "input_if": self.input_if,
            "output_if": self.output_if,
            "src_as": self.src_as,
            "dst_as": self.dst_as,
            "tcp_flags": self.tcp_flags,
            "tos": self.tos,
        }


@dataclass
class LatencyObservation:
    """
    A single hop-to-hop transit latency measurement derived by the correlation
    engine when the same 5-tuple is seen at two different exporters.

    src_device → dst_device is in the direction of the traffic flow (src_device
    is upstream, dst_device is downstream — i.e. the flow arrived at src_device
    first, then at dst_device `latency_ms` later).
    """
    src_device: str
    dst_device: str
    src_ip: str
    dst_ip: str
    src_port: int
    dst_port: int
    ip_protocol: int
    latency_ms: float
    observed_at: datetime

    def to_dict(self) -> dict[str, Any]:
        return {
            "src_device": self.src_device,
            "dst_device": self.dst_device,
            "src_ip": self.src_ip,
            "dst_ip": self.dst_ip,
            "src_port": self.src_port,
            "dst_port": self.dst_port,
            "ip_protocol": self.ip_protocol,
            "latency_ms": self.latency_ms,
            "observed_at": self.observed_at.isoformat(),
        }
