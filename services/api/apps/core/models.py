from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.db import models


class Role(models.TextChoices):
    ADMIN    = "admin",    "Admin"
    ENGINEER = "engineer", "Engineer"
    VIEWER   = "viewer",   "Viewer"
    API      = "api",      "API Service Account"


class NetPulseUser(AbstractUser):
    """
    Custom user model. Adds a role field used for RBAC throughout the API.

    Roles:
      admin    – full access to all endpoints and the Django admin panel
      engineer – read/write on all operational endpoints
      viewer   – read-only (safe HTTP methods only)
      api      – service-account tokens for integrations (read/write, no admin panel)
    """
    role = models.CharField(
        max_length=10,
        choices=Role.choices,
        default=Role.VIEWER,
        db_index=True,
    )

    # Set on the seeded initial admin (default password) so the UI forces a
    # password change on first login. Cleared once the user picks a new password.
    must_change_password = models.BooleanField(default=False)

    class Meta(AbstractUser.Meta):
        swappable = "AUTH_USER_MODEL"

    def __str__(self):
        return f"{self.username} ({self.role})"

    @property
    def is_admin(self) -> bool:
        return self.is_superuser or self.role == Role.ADMIN

    @property
    def can_write(self) -> bool:
        return self.is_superuser or self.role in (Role.ADMIN, Role.ENGINEER, Role.API)


class TimestampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True
        ordering = ["-created_at"]


class SystemSetting(TimestampedModel):
    """Simple key/value store for platform-level runtime settings.

    Used for settings that admins can change at runtime (no redeploy) and that
    fall back to environment-based defaults when unset — e.g. hostname display.
    """

    key = models.CharField(max_length=128, unique=True, db_index=True)
    value = models.CharField(max_length=512, blank=True)

    class Meta(TimestampedModel.Meta):
        verbose_name = "system setting"
        verbose_name_plural = "system settings"

    def __str__(self):
        return f"{self.key}={self.value}"

    @classmethod
    def get(cls, key, default=None):
        obj = cls.objects.filter(key=key).first()
        return obj.value if obj is not None else default

    @classmethod
    def set(cls, key, value):
        obj, _ = cls.objects.update_or_create(key=key, defaults={"value": value})
        return obj


class UserPreferences(TimestampedModel):
    """Per-user UI preferences (theme, log viewer defaults, table sizing, etc.)."""

    class Theme(models.TextChoices):
        LIGHT  = "light",  "Light"
        DARK   = "dark",   "Dark"
        SYSTEM = "system", "System"

    class LogRange(models.TextChoices):
        M15 = "15m", "Last 15 minutes"
        H1  = "1h",  "Last 1 hour"
        H4  = "4h",  "Last 4 hours"
        H12 = "12h", "Last 12 hours"
        H24 = "24h", "Last 24 hours"
        D7  = "7d",  "Last 7 days"
        ALL = "all", "All time"

    class DateFormat(models.TextChoices):
        ISO   = "iso",   "2026-05-30"
        US    = "us",    "05/30/2026"
        EU    = "eu",    "30/05/2026"

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="preferences",
    )

    # UI
    theme = models.CharField(max_length=10, choices=Theme.choices, default=Theme.SYSTEM)

    # Log viewer
    log_default_time_range = models.CharField(max_length=4, choices=LogRange.choices, default=LogRange.H1)
    log_default_page_size = models.IntegerField(default=50)
    log_auto_refresh = models.BooleanField(default=False)

    # Tables
    devices_default_columns = models.JSONField(default=list, blank=True)
    devices_page_size = models.IntegerField(default=25)

    # Display
    timezone = models.CharField(max_length=64, default="UTC")
    date_format = models.CharField(max_length=4, choices=DateFormat.choices, default=DateFormat.ISO)

    # Notifications
    email_alerts = models.BooleanField(default=True)
    # Chat handles for alert routing — used to DM / @mention the user when their
    # team is notified (see apps.alerting.engine.get_team_notification_targets).
    slack_user_id = models.CharField(max_length=64, blank=True)
    discord_user_id = models.CharField(max_length=64, blank=True)

    # Onboarding: set once the user dismisses/finishes the Get Started wizard so
    # it isn't shown again. (The wizard is also hidden system-wide once any
    # device exists — see the onboarding-status endpoint.)
    onboarding_completed = models.BooleanField(default=False)

    class Meta:
        verbose_name = "user preferences"
        verbose_name_plural = "user preferences"

    def __str__(self):
        return f"preferences for {self.user}"

    @classmethod
    def for_user(cls, user):
        obj, _ = cls.objects.get_or_create(user=user)
        return obj


class AuditLog(models.Model):
    """An immutable record of a security/operationally-significant action.

    Written via apps.core.audit.log_event from views, signals and tasks. Never
    edited after creation; pruned by retention (see run_scheduler).
    """

    class EventType(models.TextChoices):
        # Auth
        LOGIN_SUCCESS = "login_success", "Login Success"
        LOGIN_FAILED = "login_failed", "Login Failed"
        LOGOUT = "logout", "Logout"
        PASSWORD_CHANGED = "password_changed", "Password Changed"
        PASSWORD_RESET = "password_reset", "Password Reset"
        # User management
        USER_CREATED = "user_created", "User Created"
        USER_UPDATED = "user_updated", "User Updated"
        USER_DELETED = "user_deleted", "User Deleted"
        USER_ROLE_CHANGED = "user_role_changed", "User Role Changed"
        # Device management
        DEVICE_CREATED = "device_created", "Device Created"
        DEVICE_UPDATED = "device_updated", "Device Updated"
        DEVICE_DELETED = "device_deleted", "Device Deleted"
        DEVICE_APPROVED = "device_approved", "Device Approved"
        DEVICE_REJECTED = "device_rejected", "Device Rejected"
        # Configuration
        CONFIG_PUSHED = "config_pushed", "Config Pushed to Device"
        CONFIG_BACKUP = "config_backup", "Config Backup Taken"
        CONFIG_RESTORED = "config_restored", "Config Restored"
        COMPLIANCE_RUN = "compliance_run", "Compliance Check Run"
        # Credentials
        CREDENTIAL_CREATED = "credential_created", "Credential Created"
        CREDENTIAL_UPDATED = "credential_updated", "Credential Updated"
        CREDENTIAL_DELETED = "credential_deleted", "Credential Deleted"
        CREDENTIAL_ACCESSED = "credential_accessed", "Credential Accessed"
        # Discovery
        DISCOVERY_STARTED = "discovery_started", "Discovery Started"
        DISCOVERY_COMPLETED = "discovery_completed", "Discovery Completed"
        # Integrations
        NETBOX_IMPORT = "netbox_import", "NetBox Import"
        UNIFI_SYNC = "unifi_sync", "UniFi Sync"
        # System
        SETTINGS_CHANGED = "settings_changed", "Settings Changed"
        API_KEY_CREATED = "api_key_created", "API Key Created"
        API_KEY_DELETED = "api_key_deleted", "API Key Deleted"
        SSO_CONFIG_CHANGED = "sso_config_changed", "SSO Config Changed"
        # Alerts
        ALERT_ACKNOWLEDGED = "alert_acknowledged", "Alert Acknowledged"
        ALERT_RESOLVED = "alert_resolved", "Alert Resolved"
        ALERT_RULE_CREATED = "alert_rule_created", "Alert Rule Created"
        ALERT_RULE_UPDATED = "alert_rule_updated", "Alert Rule Updated"

    event_type = models.CharField(max_length=64, choices=EventType.choices, db_index=True)

    # Actor
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="audit_logs")
    username = models.CharField(max_length=150, blank=True,
                                help_text="Snapshot of the username at event time.")
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=256, blank=True)

    # Target
    target_type = models.CharField(max_length=64, blank=True, db_index=True,
                                   help_text="e.g. Device, User, Credential")
    target_id = models.CharField(max_length=64, blank=True, db_index=True)
    target_name = models.CharField(max_length=256, blank=True,
                                   help_text="Snapshot of the target's name.")

    # Detail
    description = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    # Outcome
    success = models.BooleanField(default=True, db_index=True)
    error_message = models.CharField(max_length=512, blank=True)

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["event_type", "-created_at"]),
            models.Index(fields=["user", "-created_at"]),
            models.Index(fields=["target_type", "target_id"]),
        ]

    def __str__(self):
        return f"{self.created_at} {self.username or 'system'} {self.event_type}"
