import os
from dataclasses import dataclass, field


@dataclass
class Config:
    # OTLP receiver endpoints
    host: str = field(default_factory=lambda: os.environ.get("OTLP_HOST", "0.0.0.0"))
    grpc_port: int = field(default_factory=lambda: int(os.environ.get("OTLP_GRPC_PORT", "4317")))
    http_port: int = field(default_factory=lambda: int(os.environ.get("OTLP_HTTP_PORT", "4318")))

    # NATS
    nats_url: str = field(default_factory=lambda: os.environ.get("NATS_URL", "nats://nats:4222"))
    nats_user: str = field(default_factory=lambda: os.environ.get("NATS_USER", ""))
    nats_password: str = field(default_factory=lambda: os.environ.get("NATS_PASSWORD", ""))

    # NATS stream config
    stream_name: str = "TELEMETRY"
    metrics_prefix: str = "netpulse.otel"
    stream_max_age_seconds: int = field(
        default_factory=lambda: int(os.environ.get("STREAM_MAX_AGE_SECONDS", str(7 * 24 * 3600)))
    )

    log_level: str = field(default_factory=lambda: os.environ.get("LOG_LEVEL", "INFO").upper())


cfg = Config()
