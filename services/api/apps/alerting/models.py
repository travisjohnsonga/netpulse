"""
Alert routing — teams, escalation policies, routes and notification records.

Stage 1 (this module): Team/TeamMember, ContactMethod, EscalationPolicy/Step,
AlertRoute and AlertNotification — enough for route matching + email
notifications. On-call schedules and acknowledgements arrive in Stage 2.

Array-style fields use JSONField(default=list) rather than Postgres ArrayField
to match the codebase convention and keep the SQLite test DB working.
"""
from django.conf import settings
from django.db import models

from apps.core.models import TimestampedModel


class Team(TimestampedModel):
    name = models.CharField(max_length=255, unique=True)
    description = models.TextField(blank=True)
    color = models.CharField(max_length=9, default="#3b82f6", help_text="Hex colour for the UI.")
    members = models.ManyToManyField(settings.AUTH_USER_MODEL, through="TeamMember", related_name="alert_teams")
    # Stage 2: per-team Slack incoming-webhook URL for notifications.
    slack_webhook_url = models.CharField(max_length=500, blank=True)

    def __str__(self):
        return self.name


class TeamMember(TimestampedModel):
    class Role(models.TextChoices):
        MEMBER = "member", "Member"
        LEAD = "lead", "Lead"
        MANAGER = "manager", "Manager"

    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="memberships")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="team_memberships")
    role = models.CharField(max_length=10, choices=Role.choices, default=Role.MEMBER)
    notify_email = models.BooleanField(default=True)
    notify_sms = models.BooleanField(default=False)
    notify_slack = models.BooleanField(default=False)

    class Meta(TimestampedModel.Meta):
        unique_together = ["team", "user"]

    def __str__(self):
        return f"{self.user_id}@{self.team_id} ({self.role})"


class ContactMethod(TimestampedModel):
    class Type(models.TextChoices):
        EMAIL = "email", "Email"
        SMS = "sms", "SMS"
        SLACK = "slack", "Slack"
        PAGERDUTY = "pagerduty", "PagerDuty"
        WEBHOOK = "webhook", "Webhook"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="contact_methods")
    type = models.CharField(max_length=10, choices=Type.choices)
    value = models.CharField(max_length=255)
    is_primary = models.BooleanField(default=False)
    verified = models.BooleanField(default=False)

    def __str__(self):
        return f"{self.type}:{self.value}"


class EscalationPolicy(TimestampedModel):
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="policies")
    # 0 = do not repeat the escalation chain.
    repeat_interval_minutes = models.IntegerField(default=0)

    def __str__(self):
        return self.name


class EscalationStep(TimestampedModel):
    class NotifyType(models.TextChoices):
        EMAIL = "email", "Email"
        SMS = "sms", "SMS"
        SLACK = "slack", "Slack"
        PAGERDUTY = "pagerduty", "PagerDuty"
        WEBHOOK = "webhook", "Webhook"
        ALL = "all", "All channels"

    policy = models.ForeignKey(EscalationPolicy, on_delete=models.CASCADE, related_name="steps")
    step_number = models.IntegerField()
    # Wait this long after the previous step before this one fires.
    delay_minutes = models.IntegerField(default=0)
    notify_team = models.ForeignKey(Team, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    notify_user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    notify_type = models.CharField(max_length=10, choices=NotifyType.choices, default=NotifyType.EMAIL)

    class Meta(TimestampedModel.Meta):
        unique_together = ["policy", "step_number"]
        ordering = ["policy", "step_number"]

    def __str__(self):
        return f"{self.policy_id} step {self.step_number}"


class AlertRoute(TimestampedModel):
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    # Lower priority value is evaluated first.
    priority = models.IntegerField(default=100, db_index=True)

    # Match conditions — AND across fields; an empty list means "match all".
    match_severity = models.JSONField(default=list, blank=True)
    match_source = models.JSONField(default=list, blank=True)
    match_device_tags = models.JSONField(default=list, blank=True)
    match_check_types = models.JSONField(default=list, blank=True)
    match_sites = models.ManyToManyField("devices.Site", blank=True, related_name="alert_routes")

    escalation_policy = models.ForeignKey(EscalationPolicy, on_delete=models.CASCADE, related_name="routes")

    suppress_during_maintenance = models.BooleanField(default=True)
    suppress_if_parent_down = models.BooleanField(default=True)

    class Meta(TimestampedModel.Meta):
        ordering = ["priority", "id"]

    def __str__(self):
        return self.name


class OnCallSchedule(TimestampedModel):
    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="schedules")
    name = models.CharField(max_length=255, default="Primary On-Call")
    timezone = models.CharField(max_length=64, default="UTC")

    def __str__(self):
        return f"{self.name} ({self.team_id})"


class OnCallShift(TimestampedModel):
    class Recurrence(models.TextChoices):
        NONE = "none", "None"
        DAILY = "daily", "Daily"
        WEEKLY = "weekly", "Weekly"

    schedule = models.ForeignKey(OnCallSchedule, on_delete=models.CASCADE, related_name="shifts")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="oncall_shifts")
    start_datetime = models.DateTimeField()
    end_datetime = models.DateTimeField()
    recurrence = models.CharField(max_length=8, choices=Recurrence.choices, default=Recurrence.NONE)
    recurrence_days = models.JSONField(default=list, blank=True)  # e.g. ["MON","WED","FRI"]

    class Meta(TimestampedModel.Meta):
        ordering = ["start_datetime"]

    def __str__(self):
        return f"{self.user_id} {self.start_datetime}–{self.end_datetime}"


class AlertAcknowledgement(TimestampedModel):
    alert_event = models.ForeignKey("alerts.AlertEvent", on_delete=models.CASCADE, related_name="acknowledgements")
    acknowledged_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="ack_alerts")
    acknowledged_at = models.DateTimeField()
    note = models.TextField(blank=True)
    snoozed_until = models.DateTimeField(null=True, blank=True)

    class Meta(TimestampedModel.Meta):
        indexes = [models.Index(fields=["alert_event", "-acknowledged_at"])]

    def __str__(self):
        return f"ack {self.alert_event_id} by {self.acknowledged_by_id}"


class AlertNotification(TimestampedModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        SENT = "sent", "Sent"
        FAILED = "failed", "Failed"
        ACKNOWLEDGED = "acknowledged", "Acknowledged"
        CANCELLED = "cancelled", "Cancelled"

    alert_event = models.ForeignKey("alerts.AlertEvent", on_delete=models.CASCADE, related_name="notifications")
    escalation_step = models.ForeignKey(EscalationStep, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    team = models.ForeignKey(Team, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    channel = models.CharField(max_length=20)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING, db_index=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    error = models.TextField(blank=True)

    class Meta(TimestampedModel.Meta):
        indexes = [models.Index(fields=["alert_event", "-created_at"])]

    def __str__(self):
        return f"notify {self.alert_event_id} via {self.channel} ({self.status})"
