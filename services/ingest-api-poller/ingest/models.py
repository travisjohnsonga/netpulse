"""
Normalized data models shared by all vendor plugins.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class VendorDevice:
    """Normalized device record from any vendor API."""

    integration_id: str
    vendor: str
    vendor_device_id: str        # vendor's own device ID
    name: str
    model: str
    serial: str
    mac: str
    status: str                  # "online" | "offline" | "alerting"
    ip_address: str
    firmware: str
    site_id: str
    site_name: str
    tags: list[str] = field(default_factory=list)
    raw: dict = field(default_factory=dict)
    collected_at: datetime = field(default_factory=_utcnow)

    def to_dict(self) -> dict:
        return {
            "integration_id": self.integration_id,
            "vendor": self.vendor,
            "vendor_device_id": self.vendor_device_id,
            "name": self.name,
            "model": self.model,
            "serial": self.serial,
            "mac": self.mac,
            "status": self.status,
            "ip_address": self.ip_address,
            "firmware": self.firmware,
            "site_id": self.site_id,
            "site_name": self.site_name,
            "tags": self.tags,
            "raw": self.raw,
            "collected_at": self.collected_at.isoformat(),
        }


@dataclass
class VendorAlert:
    """Normalized alert/event from any vendor API."""

    integration_id: str
    vendor: str
    alert_id: str
    severity: str          # "critical" | "high" | "medium" | "low" | "info"
    category: str          # "connectivity" | "performance" | "security" | "config"
    device_id: str
    device_name: str
    message: str
    occurred_at: datetime
    resolved: bool = False
    raw: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "integration_id": self.integration_id,
            "vendor": self.vendor,
            "alert_id": self.alert_id,
            "severity": self.severity,
            "category": self.category,
            "device_id": self.device_id,
            "device_name": self.device_name,
            "message": self.message,
            "occurred_at": self.occurred_at.isoformat(),
            "resolved": self.resolved,
            "raw": self.raw,
        }


@dataclass
class VendorMetric:
    """A single time-series metric point from a vendor API."""

    integration_id: str
    vendor: str
    device_id: str
    metric_name: str       # e.g. "uplink_bytes_sent", "client_count", "latency_ms"
    value: float
    unit: str
    tags: dict[str, str] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=_utcnow)

    def to_dict(self) -> dict:
        return {
            "integration_id": self.integration_id,
            "vendor": self.vendor,
            "device_id": self.device_id,
            "metric_name": self.metric_name,
            "value": self.value,
            "unit": self.unit,
            "tags": self.tags,
            "timestamp": self.timestamp.isoformat(),
        }
