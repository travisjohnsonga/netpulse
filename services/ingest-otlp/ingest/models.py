"""
Data models for normalised OTLP telemetry signals.

Each model has a to_dict() method that produces a JSON-serialisable
representation suitable for publishing to NATS JetStream.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone


def _now_utc() -> datetime:
    return datetime.now(tz=timezone.utc)


@dataclass
class OTLPMetric:
    """One metric (all data-points for a single metric name in a ResourceMetrics scope)."""

    resource_attrs: dict  # service.name, host.name, etc. from Resource
    scope_name: str
    metric_name: str
    metric_type: str  # "gauge" | "sum" | "histogram" | "summary"
    unit: str
    data_points: list[dict]  # each has: attributes, time_unix_nano, value (or buckets)
    exporter_ip: str
    received_at: datetime = field(default_factory=_now_utc)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["received_at"] = self.received_at.isoformat()
        return d


@dataclass
class OTLPLog:
    """One log record."""

    resource_attrs: dict
    scope_name: str
    severity_text: str
    severity_number: int
    body: str
    attributes: dict
    trace_id: str
    span_id: str
    timestamp_unix_nano: int
    exporter_ip: str
    received_at: datetime = field(default_factory=_now_utc)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["received_at"] = self.received_at.isoformat()
        return d


@dataclass
class OTLPTrace:
    """One span."""

    resource_attrs: dict
    scope_name: str
    trace_id: str
    span_id: str
    parent_span_id: str
    name: str
    kind: int
    start_time_unix_nano: int
    end_time_unix_nano: int
    duration_ms: float
    attributes: dict
    status_code: int
    exporter_ip: str
    received_at: datetime = field(default_factory=_now_utc)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["received_at"] = self.received_at.isoformat()
        return d
