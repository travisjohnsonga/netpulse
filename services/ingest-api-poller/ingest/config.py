import json
import os
from dataclasses import dataclass, field


@dataclass
class Config:
    host: str = field(default_factory=lambda: os.environ.get("WEBHOOK_HOST", "0.0.0.0"))
    webhook_port: int = field(default_factory=lambda: int(os.environ.get("WEBHOOK_PORT", "8080")))

    # NATS
    nats_url: str = field(default_factory=lambda: os.environ.get("NATS_URL", "nats://nats:4222"))
    nats_user: str = field(default_factory=lambda: os.environ.get("NATS_USER", ""))
    nats_password: str = field(default_factory=lambda: os.environ.get("NATS_PASSWORD", ""))

    # NATS stream config
    stream_name: str = "VENDOR_API"
    vendor_prefix: str = "netpulse.vendor"
    stream_max_age_seconds: int = field(
        default_factory=lambda: int(os.environ.get("STREAM_MAX_AGE_SECONDS", str(7 * 24 * 3600)))
    )

    # OpenBao
    openbao_addr: str = field(default_factory=lambda: os.environ.get("OPENBAO_ADDR", "http://openbao:8200"))
    openbao_token: str = field(default_factory=lambda: os.environ.get("OPENBAO_TOKEN", ""))
    cred_cache_ttl: int = field(default_factory=lambda: int(os.environ.get("CRED_CACHE_TTL", "300")))

    log_level: str = field(default_factory=lambda: os.environ.get("LOG_LEVEL", "INFO").upper())

    # JSON list of integration configs loaded from INTEGRATIONS_JSON env var.
    # Each entry: {"id": "meraki-org1", "vendor": "meraki", "cred_path": "secret/vendor/meraki-org1",
    #              "poll_interval": 60, "org_id": "123456", "enabled": true}
    integrations_json: str = field(default_factory=lambda: os.environ.get("INTEGRATIONS_JSON", "[]"))

    def load_integrations(self) -> list[dict]:
        try:
            return json.loads(self.integrations_json)
        except json.JSONDecodeError:
            return []


cfg = Config()
