from django.db import models

from apps.core.models import TimestampedModel

# Sentinel api_key_hash for the auto-registered local collector. The local
# server authenticates implicitly (it IS the platform), so it has no issued API
# key — but api_key_hash is required + unique, so the local row uses this fixed
# value. Only one local collector can exist as a result.
LOCAL_API_KEY_SENTINEL = "local-server-no-api-key"

# A heartbeat older than this marks the collector unhealthy. The local collector
# is heartbeated by run_scheduler on every tick (default 300s); the window
# comfortably exceeds one tick so a single missed beat doesn't flap it.
HEARTBEAT_HEALTHY_SECONDS = 600


class Collector(TimestampedModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending Registration"
        ACTIVE = "active", "Active"
        OFFLINE = "offline", "Offline"
        REVOKED = "revoked", "Revoked"

    class CollectorType(models.TextChoices):
        LOCAL = "local", "Local Server"
        REMOTE = "remote", "Remote Agent"

    name = models.CharField(max_length=255)
    # Local server vs a remote on-prem agent/poller. The platform auto-registers
    # exactly one LOCAL collector (itself); anything registered over the wire is
    # REMOTE.
    collector_type = models.CharField(
        max_length=10, choices=CollectorType.choices,
        default=CollectorType.REMOTE, db_index=True,
    )
    hostname = models.CharField(max_length=255, blank=True)
    location = models.CharField(max_length=255, blank=True)
    # What this collector can do, e.g. {"snmp": true, "ssh": true, "syslog":
    # true, "netflow": true, "grpc": true}.
    capabilities = models.JSONField(default=dict, blank=True)
    # Address devices send telemetry to (and that generated device configs
    # point at). May differ from remote_ip (the collector's source IP on
    # connect). Falls back to settings.COLLECTOR_IP when unset.
    collector_ip = models.GenericIPAddressField(null=True, blank=True)
    # Optional site association; a site's devices default to its collector.
    site = models.ForeignKey(
        "devices.Site", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="collectors",
    )
    # The global/default collector used when a device has no collector and its
    # site has no default. At most one should be marked default.
    is_default = models.BooleanField(default=False)
    # bcrypt hash of the API key issued at registration; never stored in plaintext
    api_key_hash = models.CharField(max_length=128, unique=True)
    # OpenBao PKI serial for the collector's mTLS certificate
    cert_serial = models.CharField(max_length=128, blank=True)
    cert_expires_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING, db_index=True)
    last_seen_at = models.DateTimeField(null=True, blank=True)
    version = models.CharField(max_length=50, blank=True)
    remote_ip = models.GenericIPAddressField(null=True, blank=True)

    def __str__(self):
        return self.name

    @property
    def is_healthy(self) -> bool:
        """True when a heartbeat was received recently (see HEARTBEAT_HEALTHY_SECONDS)."""
        if self.status in (self.Status.REVOKED, self.Status.OFFLINE) or not self.last_seen_at:
            return False
        from django.utils import timezone
        return (timezone.now() - self.last_seen_at).total_seconds() < HEARTBEAT_HEALTHY_SECONDS
