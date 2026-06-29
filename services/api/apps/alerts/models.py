from django.db import models

from apps.core.models import TimestampedModel


class AlertChannel(TimestampedModel):
    class ChannelType(models.TextChoices):
        SLACK = "slack", "Slack"
        EMAIL = "email", "Email"
        PAGERDUTY = "pagerduty", "PagerDuty"
        WEBHOOK = "webhook", "Webhook"
        TEAMS = "teams", "Microsoft Teams"

    name = models.CharField(max_length=255)
    channel_type = models.CharField(max_length=20, choices=ChannelType.choices)
    # Webhook URL / email / routing key stored here; real secrets referenced via OpenBao path.
    config = models.JSONField(default=dict)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.name} ({self.channel_type})"


class AlertRule(TimestampedModel):
    class Severity(models.TextChoices):
        CRITICAL = "critical", "Critical"
        HIGH = "high", "High"
        MEDIUM = "medium", "Medium"
        LOW = "low", "Low"
        INFO = "info", "Info"

    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    severity = models.CharField(max_length=10, choices=Severity.choices, default=Severity.MEDIUM, db_index=True)
    condition = models.JSONField()
    channels = models.ManyToManyField(AlertChannel, blank=True, related_name="rules")
    is_active = models.BooleanField(default=True)
    cooldown_minutes = models.PositiveIntegerField(default=60)
    # Seeded default rule (see seed_alert_rules). Protected from deletion;
    # disable it by toggling is_active instead. When is_active is False the
    # alert engines skip creating events for this rule.
    is_system = models.BooleanField(default=False)

    def __str__(self):
        return self.name


class AlertEvent(TimestampedModel):
    class State(models.TextChoices):
        FIRING = "firing", "Firing"
        RESOLVED = "resolved", "Resolved"

    rule = models.ForeignKey(AlertRule, on_delete=models.CASCADE, related_name="events")
    state = models.CharField(max_length=10, choices=State.choices, default=State.FIRING, db_index=True)
    labels = models.JSONField(default=dict)
    annotations = models.JSONField(default=dict)
    resolved_at = models.DateTimeField(null=True, blank=True)
    # Who/what resolved it: "auto" (recovery transition), "user", or "" while firing.
    resolved_by = models.CharField(max_length=32, blank=True)
    resolution_note = models.TextField(blank=True)
    # Outbound-notification dedup/debounce: stamped the first time the FIRING /
    # RESOLVED transition is dispatched to channels, so a re-save or a redundant
    # dispatch call never re-notifies (a flapping alert can't spam channels).
    # See apps/alerts/dispatch.py + signals.py.
    fired_notified_at = models.DateTimeField(null=True, blank=True)
    resolved_notified_at = models.DateTimeField(null=True, blank=True)

    class Meta(TimestampedModel.Meta):
        indexes = [models.Index(fields=["rule", "state", "-created_at"])]


class NotificationLog(TimestampedModel):
    """One row per dispatch ATTEMPT to a channel — the delivery source of truth.

    Dispatch records SUCCESS and FAILURE here so "did it deliver?" is queryable
    and a silent failure becomes visible: the delivery-health endpoint reads it,
    and a persistent failure fires a cross-channel meta-alarm. Channel identity
    is denormalized (name/type) so the log survives the channel's deletion."""

    class Status(models.TextChoices):
        SENT = "sent", "Sent"
        FAILED = "failed", "Failed"

    event = models.ForeignKey(AlertEvent, on_delete=models.CASCADE, related_name="deliveries")
    channel = models.ForeignKey(AlertChannel, null=True, blank=True,
                                on_delete=models.SET_NULL, related_name="deliveries")
    channel_name = models.CharField(max_length=255, blank=True)
    channel_type = models.CharField(max_length=20)
    transition = models.CharField(max_length=10)  # firing | resolved
    status = models.CharField(max_length=8, choices=Status.choices, db_index=True)
    attempts = models.PositiveSmallIntegerField(default=1)
    detail = models.TextField(blank=True)

    class Meta(TimestampedModel.Meta):
        indexes = [
            models.Index(fields=["channel", "-created_at"]),
            models.Index(fields=["status", "-created_at"]),
        ]

    def __str__(self):
        return f"{self.channel_type} {self.status} (event {self.event_id})"
