import os
from dataclasses import dataclass, field
from urllib.parse import quote


def _valkey_url() -> str:
    """
    Build a Valkey/Redis URL. Prefer an explicit VALKEY_URL; otherwise assemble
    one from VALKEY_HOST/PORT/PASSWORD (the .env convention) so the password
    (Valkey runs with --requirepass) is included. The password is URL-encoded so
    special characters (@ : / etc.) don't corrupt host/port parsing.
    """
    url = os.environ.get("VALKEY_URL")
    if url:
        return url
    host = os.environ.get("VALKEY_HOST", "valkey")
    port = os.environ.get("VALKEY_PORT", "6379")
    password = os.environ.get("VALKEY_PASSWORD", "")
    auth = f":{quote(password, safe='')}@" if password else ""
    return f"redis://{auth}{host}:{port}/0"


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

    # Valkey — gNMI liveness heartbeat so ingest-snmp can suppress redundant
    # SNMP polling while a device is actively streaming gNMI.
    valkey_url: str = field(default_factory=_valkey_url)
    # Key TTL — 3× the default 30s gNMI sample interval, so the key auto-expires
    # (and SNMP resumes) shortly after a stream stops.
    gnmi_heartbeat_ttl: int = field(default_factory=lambda: int(os.environ.get("GNMI_HEARTBEAT_TTL", "180")))

    # Logging
    log_level: str = field(default_factory=lambda: os.environ.get("LOG_LEVEL", "INFO").upper())


cfg = Config()
