import os
from dataclasses import dataclass, field


@dataclass
class Config:
    # gRPC server
    grpc_host: str = field(default_factory=lambda: os.environ.get("GRPC_HOST", "0.0.0.0"))
    grpc_port: int = field(default_factory=lambda: int(os.environ.get("GRPC_PORT", "57400")))
    grpc_max_workers: int = field(default_factory=lambda: int(os.environ.get("GRPC_MAX_WORKERS", "10")))

    # NATS
    nats_url: str = field(default_factory=lambda: os.environ.get("NATS_URL", "nats://nats:4222"))
    nats_user: str = field(default_factory=lambda: os.environ.get("NATS_USER", ""))
    nats_password: str = field(default_factory=lambda: os.environ.get("NATS_PASSWORD", ""))

    # JetStream
    stream_name: str = "TELEMETRY"
    subject_prefix: str = "netpulse.telemetry"
    # Maximum message age in seconds (7 days)
    stream_max_age_seconds: int = field(
        default_factory=lambda: int(os.environ.get("STREAM_MAX_AGE_SECONDS", str(7 * 24 * 3600)))
    )

    # Logging
    log_level: str = field(default_factory=lambda: os.environ.get("LOG_LEVEL", "INFO").upper())


cfg = Config()
