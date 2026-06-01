import json
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
    # Trap receiver
    host: str = field(default_factory=lambda: os.environ.get("SNMP_HOST", "0.0.0.0"))
    trap_port: int = field(default_factory=lambda: int(os.environ.get("SNMP_TRAP_PORT", "162")))

    # SNMP poller defaults
    poll_timeout: float = field(default_factory=lambda: float(os.environ.get("SNMP_POLL_TIMEOUT", "5")))
    poll_retries: int = field(default_factory=lambda: int(os.environ.get("SNMP_POLL_RETRIES", "1")))

    # Community strings accepted for incoming traps (comma-separated)
    trap_communities: list[str] = field(
        default_factory=lambda: os.environ.get("SNMP_TRAP_COMMUNITIES", "public").split(",")
    )

    # Devices JSON — JSON array of device dicts loaded on startup.
    # Live updates arrive via NATS netpulse.devices.upsert / netpulse.devices.remove.
    devices_json: str = field(default_factory=lambda: os.environ.get("DEVICES_JSON", "[]"))

    # NATS
    nats_url: str = field(default_factory=lambda: os.environ.get("NATS_URL", "nats://nats:4222"))
    nats_user: str = field(default_factory=lambda: os.environ.get("NATS_USER", ""))
    nats_password: str = field(default_factory=lambda: os.environ.get("NATS_PASSWORD", ""))

    # NATS stream config
    stream_name: str = "TELEMETRY"
    metrics_prefix: str = "netpulse.telemetry"
    stream_max_age_seconds: int = field(
        default_factory=lambda: int(os.environ.get("STREAM_MAX_AGE_SECONDS", str(7 * 24 * 3600)))
    )

    # OpenBao
    openbao_addr: str = field(default_factory=lambda: os.environ.get("OPENBAO_ADDR", "http://openbao:8200"))
    openbao_token: str = field(default_factory=lambda: os.environ.get("OPENBAO_TOKEN", ""))
    cred_cache_ttl: int = field(default_factory=lambda: int(os.environ.get("CRED_CACHE_TTL", "300")))

    log_level: str = field(default_factory=lambda: os.environ.get("LOG_LEVEL", "INFO").upper())

    # ── Adaptive polling (gNMI/SNMP) ──────────────────────────────────────────
    # When a device is actively streaming gNMI (heartbeat in Valkey), suppress
    # redundant SNMP polling. Disable with ADAPTIVE_POLLING=false to always poll.
    adaptive_polling: bool = field(
        default_factory=lambda: os.environ.get("ADAPTIVE_POLLING", "true").lower() != "false"
    )
    valkey_url: str = field(default_factory=_valkey_url)
    # A heartbeat newer than this many seconds counts as "gNMI active". Kept
    # below the 180s heartbeat TTL so SNMP resumes ~1 missed interval after a
    # stream stalls, before the key actually expires.
    gnmi_active_threshold: int = field(
        default_factory=lambda: int(os.environ.get("GNMI_ACTIVE_THRESHOLD", "120"))
    )

    def load_devices(self) -> list[dict]:
        try:
            return json.loads(self.devices_json)
        except json.JSONDecodeError:
            return []


cfg = Config()
