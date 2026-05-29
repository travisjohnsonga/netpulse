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
