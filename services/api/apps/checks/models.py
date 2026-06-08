"""
Agentless service checking / synthetic monitoring.

NetPulse probes services externally (no agent on the target). A ServiceCheck
describes *what* to probe and *how often*; the check-engine management command
runs due checks, writes a CheckResult per probe and maintains the check's
current status, raising NATS alerts on state changes.

Stage 1 implements HTTP/HTTPS and TCP handlers (see ``runner.py``). The model
already carries every planned check_type so later stages only add handlers.
"""
from django.db import models

from apps.core.models import TimestampedModel


class ServiceCheck(TimestampedModel):
    class CheckType(models.TextChoices):
        HTTP = "http", "HTTP"
        HTTPS = "https", "HTTPS"
        TCP = "tcp", "TCP"
        UDP = "udp", "UDP"
        ICMP = "icmp", "ICMP (ping)"
        DNS = "dns", "DNS"
        TLS = "tls", "TLS certificate"
        SMTP = "smtp", "SMTP"
        FTP = "ftp", "FTP"
        SSH = "ssh", "SSH"
        SSH_BANNER = "ssh_banner", "SSH banner"
        LDAP = "ldap", "LDAP"
        RADIUS = "radius", "RADIUS"
        TACACS = "tacacs", "TACACS+"
        CUSTOM = "custom", "Custom"

    class Status(models.TextChoices):
        UP = "up", "Up"
        DOWN = "down", "Down"
        DEGRADED = "degraded", "Degraded"
        UNKNOWN = "unknown", "Unknown"

    class CollectorMode(models.TextChoices):
        ALL = "all", "All Collectors"          # must pass from every collector
        ANY = "any", "Any One Collector"       # pass if any collector succeeds
        SELECTED = "selected", "Selected Collectors"  # run from specific collectors
        SITE = "site", "Same Site as Device"   # collectors at the device's site

    # Default port per check type (None → required in config / not port-based).
    DEFAULT_PORTS = {
        "http": 80, "https": 443, "ssh": 22, "ssh_banner": 22, "smtp": 25,
        "dns": 53, "ftp": 21, "ldap": 389, "tls": 443,
        "radius": 1812,   # UDP auth (1813 = accounting)
        "tacacs": 49,     # TCP
    }

    name = models.CharField(max_length=255)
    check_type = models.CharField(max_length=10, choices=CheckType.choices, db_index=True)

    # Target
    host = models.CharField(max_length=255, help_text="IP or hostname to probe.")
    port = models.IntegerField(null=True, blank=True, help_text="Defaults from check_type when unset.")

    # Schedule
    interval_seconds = models.IntegerField(default=60)
    timeout_seconds = models.IntegerField(default=10)

    # Associations — all optional, mix and match. A check can hang off a network
    # device/server, a site, both, or neither.
    device = models.ForeignKey(
        "devices.Device", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="service_checks",
        help_text="Network device/server this check is associated with.",
    )
    site = models.ForeignKey(
        "devices.Site", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="service_checks",
        help_text="Site this check belongs to.",
    )

    # State
    is_active = models.BooleanField(default=True)
    is_enabled = models.BooleanField(default=True, help_text="Pause scheduling without deleting.")
    current_status = models.CharField(max_length=10, choices=Status.choices, default=Status.UNKNOWN, db_index=True)
    last_checked = models.DateTimeField(null=True, blank=True)
    last_status_change = models.DateTimeField(null=True, blank=True)
    consecutive_failures = models.IntegerField(default=0)
    failures_before_alert = models.IntegerField(default=2)

    # Which state-change alerts to raise (NATS) for this check.
    alert_on_down = models.BooleanField(default=True)
    alert_on_recovery = models.BooleanField(default=True)
    alert_on_degraded = models.BooleanField(default=False)

    # Per-type configuration (method, expected_status, query, warn_days, …).
    config = models.JSONField(default=dict, blank=True)

    # Response-time thresholds → degraded / down classification (optional).
    response_time_warning_ms = models.IntegerField(null=True, blank=True)
    response_time_critical_ms = models.IntegerField(null=True, blank=True)

    tags = models.JSONField(default=list, blank=True)
    notes = models.TextField(blank=True)

    # Multi-vantage-point execution: which collectors run this check. The mode
    # decides resolution + result aggregation (see apps.checks.collectors).
    collector_mode = models.CharField(
        max_length=20, choices=CollectorMode.choices, default=CollectorMode.SITE,
        help_text="all=pass from every collector · any=pass if one passes · "
                  "selected=specific collectors · site=collectors at device's site",
    )
    collectors = models.ManyToManyField(
        "collectors.Collector", blank=True, through="ServiceCheckCollector",
        related_name="service_checks",
        help_text="Collectors that run this check (used by the 'selected' mode).",
    )

    class Meta(TimestampedModel.Meta):
        indexes = [models.Index(fields=["check_type", "current_status"])]

    def __str__(self):
        return f"{self.name} ({self.check_type})"

    @property
    def effective_port(self):
        """Configured port, else the well-known default for the check type."""
        if self.port:
            return self.port
        return self.DEFAULT_PORTS.get(self.check_type)


class ServiceCheckCollector(TimestampedModel):
    """Through model for ServiceCheck ↔ Collector, with per-collector state.

    Each row is one vantage point's view of a check: its latest result, latency
    and failure streak. Updated as each collector reports (centrally today; per
    remote agent once distributed pollers land).
    """

    class Result(models.TextChoices):
        PASSING = "passing", "Passing"
        FAILING = "failing", "Failing"
        UNKNOWN = "unknown", "Unknown"

    service_check = models.ForeignKey(
        ServiceCheck, on_delete=models.CASCADE, related_name="collector_assignments")
    collector = models.ForeignKey(
        "collectors.Collector", on_delete=models.CASCADE, related_name="check_assignments")
    enabled = models.BooleanField(default=True)

    # Per-collector result tracking.
    last_result = models.CharField(max_length=20, choices=Result.choices, default=Result.UNKNOWN)
    last_checked = models.DateTimeField(null=True, blank=True)
    last_latency_ms = models.FloatField(null=True, blank=True)
    last_error = models.CharField(max_length=512, blank=True)
    consecutive_failures = models.IntegerField(default=0)

    class Meta(TimestampedModel.Meta):
        unique_together = [["service_check", "collector"]]
        indexes = [models.Index(fields=["service_check", "collector"])]

    def __str__(self):
        return f"{self.service_check_id}@{self.collector_id}={self.last_result}"


class CheckResult(TimestampedModel):
    # Named ``service_check`` (not ``check``) because Django reserves
    # Model.check(); the API still exposes it as "check" via the serializer.
    service_check = models.ForeignKey(ServiceCheck, on_delete=models.CASCADE, related_name="results")
    # The collector (vantage point) that produced this result. Null for legacy
    # rows and single-location checks executed by the central engine.
    collector = models.ForeignKey(
        "collectors.Collector", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="check_results")
    status = models.CharField(max_length=10, choices=ServiceCheck.Status.choices)
    response_time_ms = models.FloatField(null=True, blank=True)
    checked_at = models.DateTimeField(db_index=True)
    error = models.CharField(max_length=512, blank=True)
    details = models.JSONField(default=dict, blank=True)

    class Meta(TimestampedModel.Meta):
        indexes = [models.Index(fields=["service_check", "-checked_at"])]

    def __str__(self):
        return f"{self.service_check_id}@{self.checked_at}={self.status}"
