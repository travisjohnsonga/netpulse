"""
Configuration backup settings and stored device configs.

Git credentials live in OpenBao at ``git_vault_path``; only the path is stored.
ConfigBackupSettings is a singleton (one row, pk=1) until multi-tenancy lands —
the spec's per-tenant OneToOne collapses to a single global record for now.
"""
from django.db import models

from apps.core.models import TimestampedModel


class ConfigBackupSettings(TimestampedModel):
    class GitProvider(models.TextChoices):
        GITHUB = "github", "GitHub"
        GITLAB_CLOUD = "gitlab_cloud", "GitLab (cloud)"
        GITLAB_SELF = "gitlab_self", "GitLab (self-hosted)"
        GITEA = "gitea", "Gitea"
        BITBUCKET = "bitbucket", "Bitbucket"
        GENERIC_HTTPS = "generic_https", "Generic HTTPS"
        GENERIC_SSH = "generic_ssh", "Generic SSH"

    class AuthMethod(models.TextChoices):
        TOKEN = "token", "Personal Access Token"
        SSH_KEY = "ssh_key", "SSH Key"
        DEPLOY_KEY = "deploy_key", "Deploy Key"

    class SyncFrequency(models.TextChoices):
        ON_BACKUP = "on_backup", "On every backup"
        HOURLY = "hourly", "Hourly"
        DAILY = "daily", "Daily"

    # Local storage
    local_enabled = models.BooleanField(default=True)
    local_path = models.CharField(max_length=512, default="/opt/netpulse/configs")
    local_retention_days = models.PositiveIntegerField(default=90)

    # Git sync
    git_enabled = models.BooleanField(default=False)
    git_provider = models.CharField(max_length=20, choices=GitProvider.choices, blank=True)
    git_repo_url = models.CharField(max_length=512, blank=True)
    git_branch = models.CharField(max_length=255, default="main")
    git_auth_method = models.CharField(max_length=20, choices=AuthMethod.choices, blank=True)
    # OpenBao path for git credentials — never the credential itself.
    git_vault_path = models.CharField(max_length=512, blank=True)
    git_commit_author = models.CharField(max_length=255, default="NetPulse Config Manager")
    git_commit_email = models.EmailField(blank=True)
    git_sync_frequency = models.CharField(max_length=20, choices=SyncFrequency.choices, default=SyncFrequency.ON_BACKUP)

    last_sync_at = models.DateTimeField(null=True, blank=True)
    last_sync_success = models.BooleanField(null=True)
    last_commit_sha = models.CharField(max_length=64, blank=True)

    class Meta:
        verbose_name = "Configuration backup settings"
        verbose_name_plural = "Configuration backup settings"

    def __str__(self):
        return "Config backup settings"

    @classmethod
    def load(cls) -> "ConfigBackupSettings":
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class DeviceConfig(TimestampedModel):
    class ConfigType(models.TextChoices):
        RUNNING = "running", "Running"
        STARTUP = "startup", "Startup"
        CANDIDATE = "candidate", "Candidate"

    class CollectedBy(models.TextChoices):
        SCHEDULED = "scheduled", "Scheduled"
        MANUAL = "manual", "Manual"
        DRIFT = "drift_detected", "Drift detected"

    device = models.ForeignKey("devices.Device", on_delete=models.CASCADE, related_name="configs")
    config_type = models.CharField(max_length=10, choices=ConfigType.choices, default=ConfigType.RUNNING, db_index=True)
    collected_at = models.DateTimeField(db_index=True)
    collected_by = models.CharField(max_length=16, choices=CollectedBy.choices, default=CollectedBy.SCHEDULED)
    content = models.TextField()
    content_hash = models.CharField(max_length=64, db_index=True)  # SHA256 hex
    changed_from_previous = models.BooleanField(default=False)
    diff_summary = models.TextField(null=True, blank=True)
    git_commit_sha = models.CharField(max_length=64, blank=True)
    local_path = models.CharField(max_length=512, blank=True)
    compliance_status = models.CharField(max_length=32, blank=True)

    class Meta(TimestampedModel.Meta):
        indexes = [models.Index(fields=["device", "config_type", "-collected_at"])]

    def __str__(self):
        return f"{self.device} {self.config_type} @ {self.collected_at:%Y-%m-%d}"
