import os
from dataclasses import dataclass, field


@dataclass
class Config:
    host: str = field(default_factory=lambda: os.environ.get("FLOW_HOST", "0.0.0.0"))
    netflow_port: int = field(default_factory=lambda: int(os.environ.get("NETFLOW_PORT", "2055")))
    sflow_port: int = field(default_factory=lambda: int(os.environ.get("SFLOW_PORT", "6343")))

    # Correlation engine — window within which the same 5-tuple seen at two
    # different exporters is treated as the same flow traversing a WAN link.
    correlation_window: float = field(
        default_factory=lambda: float(os.environ.get("CORRELATION_WINDOW_SECONDS", "30"))
    )
    correlation_max_per_key: int = field(
        default_factory=lambda: int(os.environ.get("CORRELATION_MAX_PER_KEY", "20"))
    )

    # InfluxDB (writes latency observations)
    influxdb_url: str = field(default_factory=lambda: os.environ.get("INFLUXDB_URL", "http://influxdb:8086"))
    influxdb_token: str = field(default_factory=lambda: os.environ.get("INFLUXDB_ADMIN_TOKEN", ""))
    influxdb_org: str = field(default_factory=lambda: os.environ.get("INFLUXDB_ORG", "netpulse"))
    influxdb_bucket: str = field(default_factory=lambda: os.environ.get("INFLUXDB_BUCKET", "metrics"))

    # NATS
    nats_url: str = field(default_factory=lambda: os.environ.get("NATS_URL", "nats://nats:4222"))
    nats_user: str = field(default_factory=lambda: os.environ.get("NATS_USER", ""))
    nats_password: str = field(default_factory=lambda: os.environ.get("NATS_PASSWORD", ""))

    stream_name: str = "FLOWS"
    flows_prefix: str = "netpulse.flows"
    stream_max_age_seconds: int = field(
        default_factory=lambda: int(os.environ.get("STREAM_MAX_AGE_SECONDS", str(7 * 24 * 3600)))
    )

    log_level: str = field(default_factory=lambda: os.environ.get("LOG_LEVEL", "INFO").upper())


cfg = Config()
