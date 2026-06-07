"""
External platform integrations.

NetBoxImport records the outcome of a NetBox inventory import. The NetBox API
token is written to OpenBao at ``vault_path``; only the path is stored here.
"""
from django.contrib.auth import get_user_model
from django.db import models

from apps.core.models import TimestampedModel


class NetBoxImport(TimestampedModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        RUNNING = "running", "Running"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"

    netbox_url = models.URLField()
    netbox_version = models.CharField(max_length=32, blank=True)
    # OpenBao path holding the API token (never the token itself).
    vault_path = models.CharField(max_length=512, blank=True)
    options = models.JSONField(default=dict)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING, db_index=True)

    sites_imported = models.PositiveIntegerField(default=0)
    devices_imported = models.PositiveIntegerField(default=0)
    devices_updated = models.PositiveIntegerField(default=0)
    skipped = models.PositiveIntegerField(default=0)
    errors = models.JSONField(default=list)

    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        get_user_model(), null=True, blank=True,
        on_delete=models.SET_NULL, related_name="netbox_imports",
    )

    class Meta(TimestampedModel.Meta):
        indexes = [models.Index(fields=["status", "-created_at"])]

    def __str__(self):
        return f"NetBox import {self.netbox_url} ({self.status})"


# OpenBao path holding the SMTP password (key "password"); never stored in the DB.
SMTP_VAULT_PATH = "netpulse/integrations/smtp"


class EmailSettings(TimestampedModel):
    """
    Singleton SMTP configuration for outbound alert email, editable from
    Settings → Integrations. The password lives in OpenBao (SMTP_VAULT_PATH);
    only non-secret connection settings are stored here. Use ``load()`` to fetch
    the single row.
    """
    class Provider(models.TextChoices):
        CUSTOM = "custom", "Custom SMTP"
        GMAIL = "gmail", "Gmail"
        MICROSOFT365 = "m365", "Microsoft 365"
        SENDGRID = "sendgrid", "SendGrid"
        MAILGUN = "mailgun", "Mailgun"

    provider = models.CharField(max_length=20, choices=Provider.choices, default=Provider.CUSTOM)
    host = models.CharField(max_length=255, blank=True)
    port = models.IntegerField(default=587)
    username = models.CharField(max_length=255, blank=True)
    use_tls = models.BooleanField(default=True)
    use_ssl = models.BooleanField(default=False)
    from_email = models.CharField(max_length=255, blank=True)
    from_name = models.CharField(max_length=128, default="NetPulse")
    enabled = models.BooleanField(default=False)

    class Meta:
        verbose_name = "Email Settings"
        verbose_name_plural = "Email Settings"

    def __str__(self):
        return f"EmailSettings({self.provider}, {'enabled' if self.enabled else 'disabled'})"

    @classmethod
    def load(cls) -> "EmailSettings":
        obj = cls.objects.first()
        if obj is None:
            obj = cls.objects.create()
        return obj
