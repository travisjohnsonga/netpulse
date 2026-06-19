"""
Platform backup/restore models.

Two models, both secret-free:

* ``BackupConfig`` — a singleton (pk=1) holding the schedule + what to include +
  the destination connection settings. Destination secrets (SCP password/key,
  Git SSH key, S3 access/secret) and the scheduled-backup encryption password
  live ONLY in OpenBao under ``spane/backup/{scp,git,s3,encryption}`` — never in
  the DB and never returned by the API.

* ``BackupRecord`` — one row per backup attempt (manual or scheduled). It records
  outcome metadata only; the encryption password is NEVER stored — at most a
  non-secret ``encryption_hint`` the operator chose.

Mirrors the singleton-settings pattern used by apps.integrations (EmailSettings,
MistIntegration): ``load()`` fetches/creates the single row.
"""
from __future__ import annotations

from django.db import models

# OpenBao paths for destination secrets + the scheduled-backup encryption
# password. Only the *paths* live in code; the values live in OpenBao. Key names
# are documented next to each path.
SCP_VAULT_PATH = "spane/backup/scp"          # keys: "password", "ssh_key"
GIT_VAULT_PATH = "spane/backup/git"          # key:  "ssh_key"
S3_VAULT_PATH = "spane/backup/s3"            # keys: "access_key", "secret_key"
ENCRYPTION_VAULT_PATH = "spane/backup/encryption"  # key: "password"


class BackupConfig(models.Model):
    """Singleton backup configuration (pk=1). Use ``load()`` to fetch it."""

    class Schedule(models.TextChoices):
        DISABLED = "disabled", "Disabled"
        DAILY = "daily", "Daily"
        WEEKLY = "weekly", "Weekly"
        MONTHLY = "monthly", "Monthly"

    class Destination(models.TextChoices):
        LOCAL = "local", "Local filesystem"
        SCP = "scp", "SCP / SFTP"
        GIT = "git", "Git repository"
        S3 = "s3", "S3 / object storage"

    # ── schedule ──────────────────────────────────────────────────────────────
    schedule = models.CharField(max_length=16, choices=Schedule.choices, default=Schedule.DISABLED)
    schedule_time = models.TimeField(default="02:00", help_text="Time of day (server local) to run")
    schedule_day = models.IntegerField(
        null=True, blank=True,
        help_text="Weekly: 0=Mon..6=Sun. Monthly: 1-28. Ignored for daily.",
    )
    retention_days = models.PositiveIntegerField(default=30)

    # ── what to include ───────────────────────────────────────────────────────
    include_postgres = models.BooleanField(default=True)
    include_influxdb = models.BooleanField(default=False)
    include_openbao = models.BooleanField(default=True)
    include_config_files = models.BooleanField(default=True)
    include_ssl_certs = models.BooleanField(default=True)
    include_influxdb_days = models.PositiveIntegerField(default=30)

    # ── destination ───────────────────────────────────────────────────────────
    local_path = models.CharField(max_length=512, default="/opt/spane/backups")
    destination = models.CharField(max_length=8, choices=Destination.choices, default=Destination.LOCAL)

    # SCP / SFTP (password / key in OpenBao at SCP_VAULT_PATH)
    scp_host = models.CharField(max_length=255, blank=True)
    scp_port = models.IntegerField(default=22)
    scp_username = models.CharField(max_length=128, blank=True)
    scp_path = models.CharField(max_length=512, blank=True)

    # Git (ssh key in OpenBao at GIT_VAULT_PATH)
    git_repo_url = models.CharField(max_length=512, blank=True)
    git_branch = models.CharField(max_length=128, default="main")
    git_path = models.CharField(max_length=256, default="spane/")

    # S3 / object storage (access/secret in OpenBao at S3_VAULT_PATH)
    s3_bucket = models.CharField(max_length=255, blank=True)
    s3_prefix = models.CharField(max_length=256, default="spane-backups/")
    s3_endpoint = models.CharField(max_length=255, blank=True)
    s3_region = models.CharField(max_length=64, default="us-east-1")

    # Encryption is mandatory by default; sensitive backups always require a
    # password (enforced in the API + the backup script regardless of this flag).
    encryption_required = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Backup Configuration"
        verbose_name_plural = "Backup Configuration"

    def __str__(self):
        return f"BackupConfig(schedule={self.schedule}, dest={self.destination})"

    def save(self, *args, **kwargs):
        # Enforce singleton: always pk=1.
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def load(cls) -> "BackupConfig":
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class BackupRecord(models.Model):
    """One backup attempt (manual or scheduled). Secret-free by construction."""

    class Status(models.TextChoices):
        RUNNING = "running", "Running"
        SUCCESS = "success", "Success"
        FAILED = "failed", "Failed"
        PARTIAL = "partial", "Partial"

    started_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.RUNNING)
    triggered_by = models.CharField(max_length=32, default="scheduled")
    components = models.JSONField(default=dict)
    filename = models.CharField(max_length=512, blank=True)
    file_size_bytes = models.BigIntegerField(null=True, blank=True)
    local_path = models.CharField(max_length=1024, blank=True)
    remote_path = models.CharField(max_length=1024, blank=True)
    error_message = models.TextField(blank=True)
    duration_seconds = models.IntegerField(null=True, blank=True)
    # True when the archive was encrypted with a password. The password itself is
    # NEVER stored — only the operator-chosen, non-secret hint.
    encrypted = models.BooleanField(default=False)
    encryption_hint = models.CharField(max_length=256, blank=True)

    class Meta:
        ordering = ["-started_at"]

    def __str__(self):
        return f"BackupRecord({self.started_at:%Y-%m-%d %H:%M}, {self.status})"
