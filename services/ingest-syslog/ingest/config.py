import os
from dataclasses import dataclass, field


@dataclass
class Config:
    host: str = field(default_factory=lambda: os.environ.get("SYSLOG_HOST", "0.0.0.0"))
    udp_port: int = field(default_factory=lambda: int(os.environ.get("SYSLOG_UDP_PORT", "514")))
    tcp_port: int = field(default_factory=lambda: int(os.environ.get("SYSLOG_TCP_PORT", "601")))

    # Maximum bytes read per TCP line; protects against slow-loris / runaway senders.
    tcp_max_line: int = field(
        default_factory=lambda: int(os.environ.get("SYSLOG_TCP_MAX_LINE", str(64 * 1024)))
    )

    nats_url: str = field(default_factory=lambda: os.environ.get("NATS_URL", "nats://nats:4222"))
    nats_user: str = field(default_factory=lambda: os.environ.get("NATS_USER", ""))
    nats_password: str = field(default_factory=lambda: os.environ.get("NATS_PASSWORD", ""))

    stream_name: str = "LOGS"
    subject_prefix: str = "netpulse.logs"
    stream_max_age_seconds: int = field(
        default_factory=lambda: int(os.environ.get("STREAM_MAX_AGE_SECONDS", str(30 * 24 * 3600)))
    )

    log_level: str = field(default_factory=lambda: os.environ.get("LOG_LEVEL", "INFO").upper())


cfg = Config()
