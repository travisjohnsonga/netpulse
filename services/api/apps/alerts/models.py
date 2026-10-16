from django.conf import settings
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


# Tier-1 SYSTEM rules: spane monitoring its OWN health/machinery (the cross-
# channel notification-delivery meta-alarm + any future engine/scheduler/
# collector/dispatch self-health rules). Everything else is Tier-2 OPERATIONAL
# (spane monitoring the customer's network/servers). Names here classify as
# system; the seed backfill migration and the dispatch meta-alarm reference it.
SYSTEM_TIER_RULE_NAMES = frozenset({"Notification Delivery Failed"})


class AlertRule(TimestampedModel):
    class Severity(models.TextChoices):
        CRITICAL = "critical", "Critical"
        HIGH = "high", "High"
        MEDIUM = "medium", "Medium"
        LOW = "low", "Low"
        INFO = "info", "Info"

    class Kind(models.TextChoices):
        # Tier 1 — spane-monitoring-spane's-own-health (platform machinery).
        SYSTEM = "system", "System"
        # Tier 2 — spane-monitoring-the-customer's-network/servers.
        OPERATIONAL = "operational", "Operational"

    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    severity = models.CharField(max_length=10, choices=Severity.choices, default=Severity.MEDIUM, db_index=True)
    condition = models.JSONField()
    channels = models.ManyToManyField(AlertChannel, blank=True, related_name="rules")
    is_active = models.BooleanField(default=True)
    cooldown_minutes = models.PositiveIntegerField(default=60)
    # Generation-vs-notification split at the RULE level: when False the rule
    # still CREATES AlertEvents (they show in the UI / Alerts list) but dispatch
    # is skipped — no email/Teams (observe-only). Distinct from is_active=False,
    # which disables the rule entirely. Enforced in dispatch.py:dispatch_event.
    notify_enabled = models.BooleanField(default=True)
    # Two-tier classification by WHAT the rule monitors (rule-management arc):
    # SYSTEM = spane's own machinery, OPERATIONAL = the customer's network. This
    # is the source of truth for the kind badge; a later PR will make delete/
    # disable protection kind-aware and retire is_system.
    kind = models.CharField(
        max_length=16, choices=Kind.choices, default=Kind.OPERATIONAL, db_index=True)
    # Seeded default rule (see seed_alert_rules). Protected from deletion;
    # disable it by toggling is_active instead. When is_active is False the
    # alert engines skip creating events for this rule. NOTE: this stays the
    # protection flag for now — orthogonal to `kind`, which only classifies.
    is_system = models.BooleanField(default=False)
    # Provenance (first landed by clone-to-custom): the user who created this
    # rule. Null for seeded/engine/migration rules (no human author). A later
    # provenance PR surfaces "created by/on" in the UI; the clone action sets it
    # so a copied rule records who made it.
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="created_alert_rules")

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
