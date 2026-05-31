from django.db import models

from apps.core.models import TimestampedModel


class AlertChannel(TimestampedModel):
    class ChannelType(models.TextChoices):
        SLACK = "slack", "Slack"
        EMAIL = "email", "Email"
        PAGERDUTY = "pagerduty", "PagerDuty"
        WEBHOOK = "webhook", "Webhook"

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

    class Meta(TimestampedModel.Meta):
        indexes = [models.Index(fields=["rule", "state", "-created_at"])]
