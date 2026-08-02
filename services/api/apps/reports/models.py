"""
Report generation: stored report artifacts + delivery schedules.

GeneratedReport is the history of produced files (PDF/CSV/JSON/HTML) under
MEDIA_ROOT/reports/{year}/{month}/. ReportSchedule drives recurring generation +
email delivery (the run_scheduler loop checks for due schedules each tick).
"""
from django.conf import settings
from django.db import models

from apps.core.models import TimestampedModel


class ReportType(models.TextChoices):
    COMPLIANCE_SUMMARY = "compliance_summary", "Compliance Summary"
    DAILY_OPS = "daily_ops", "Daily Operations"


class GeneratedReport(models.Model):
    report_type = models.CharField(max_length=32, choices=ReportType.choices, db_index=True)
    generated_at = models.DateTimeField(auto_now_add=True, db_index=True)
    generated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="generated_reports")
    # 'scheduled' when produced by a ReportSchedule, else the username/'on-demand'.
    source = models.CharField(max_length=32, default="on-demand")
    parameters = models.JSONField(default=dict)
    file_path = models.CharField(max_length=512, blank=True)
    file_size = models.IntegerField(null=True, blank=True)
    format = models.CharField(max_length=10, default="pdf")

    class Meta:
        ordering = ["-generated_at"]
        indexes = [models.Index(fields=["report_type", "-generated_at"])]

    def __str__(self):
        return f"{self.get_report_type_display()} ({self.format}) @ {self.generated_at:%Y-%m-%d %H:%M}"

    @property
    def title(self) -> str:
        return self.get_report_type_display()


class ReportSchedule(TimestampedModel):
    class Frequency(models.TextChoices):
        DAILY = "daily", "Daily"
        WEEKLY = "weekly", "Weekly"
        MONTHLY = "monthly", "Monthly"
        QUARTERLY = "quarterly", "Quarterly"

    class Delivery(models.TextChoices):
        EMAIL = "email", "Email"
        STORE_ONLY = "store_only", "Store only"
        BOTH = "both", "Email + Store"

    report_type = models.CharField(max_length=32, choices=ReportType.choices)
    frequency = models.CharField(max_length=16, choices=Frequency.choices, default=Frequency.DAILY)
    hour = models.PositiveSmallIntegerField(default=8, help_text="Hour of day (UTC), 0-23")
    day_of_week = models.PositiveSmallIntegerField(
        default=0, help_text="0=Mon … 6=Sun (weekly only)")
    day_of_month = models.PositiveSmallIntegerField(
        default=1, help_text="1-28 (monthly only)")
    fmt = models.CharField(max_length=10, default="pdf")
    # How the generated report is delivered. Every run still generates + stores a
    # downloadable GeneratedReport; `delivery` only controls whether it is ALSO
    # emailed. Defaults to EMAIL so existing schedules keep emailing (back-compat).
    delivery = models.CharField(
        max_length=16, choices=Delivery.choices, default=Delivery.EMAIL,
        help_text="email | store_only | both")
    recipients = models.JSONField(default=list, help_text="Email addresses (required for email/both)")
    parameters = models.JSONField(default=dict, help_text="Report build params (site_ids, group_by, …)")
    enabled = models.BooleanField(default=True)
    last_run = models.DateTimeField(null=True, blank=True)
    last_status = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["report_type", "frequency"]

    def __str__(self):
        return f"{self.get_report_type_display()} — {self.frequency} @ {self.hour:02d}:00"

    @property
    def email_enabled(self) -> bool:
        """Whether this schedule should email the report (vs store-only)."""
        return self.delivery in (self.Delivery.EMAIL, self.Delivery.BOTH)
