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
