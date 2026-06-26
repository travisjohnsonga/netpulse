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

    # RBAC Track 2 (Phase A): roles-as-data. The legacy ``role`` CharField above
    # stays the live enforcement input for now; this FK is populated by the seed
    # migration (mapped from ``role``) and read by has_capability()/HasCapability
    # in Phase B. Kept alongside ``role`` — NOT replacing it — for rollback safety.
    # SET_NULL so deleting a (non-system) role never cascades into users.
    rbac_role = models.ForeignKey(
        "core.RBACRole", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="users",
    )

    # Set on the seeded initial admin (default password) so the UI forces a
    # password change on first login. Cleared once the user picks a new password.
    must_change_password = models.BooleanField(default=False)

    class Meta(AbstractUser.Meta):
        swappable = "AUTH_USER_MODEL"

    def save(self, *args, **kwargs):
        # RBAC Track 2 (Phase B): capabilities are resolved from ``rbac_role``,
        # but the legacy ``role`` CharField is still the management input (the
        # Users UI + serializers set it). Keep ``rbac_role`` synced to the system
        # role mapped from ``role`` so EVERY user — new ones created via the API,
        # SSO, ``createsuperuser``, or the seeded admin — resolves to the right
        # capability set, and a role change updates capabilities. An explicitly
        # assigned *custom* (non-system) role is respected and never overwritten.
        self._sync_rbac_role()
        super().save(*args, **kwargs)

    def _sync_rbac_role(self):
        from apps.core.capabilities import LEGACY_ROLE_TO_SYSTEM

        # A direct rbac_role assignment (Phase C role-management API) is
        # authoritative — never re-derive it from the legacy `role` on that save.
        if getattr(self, "_rbac_role_explicit", False):
            return
        # Respect an explicit custom (non-system) role assignment.
        if self.rbac_role_id is not None and not getattr(self.rbac_role, "is_system", False):
            return
        target = LEGACY_ROLE_TO_SYSTEM.get(self.role)
        if not target:
            return
        if self.rbac_role_id is None or self.rbac_role.name != target:
            try:
                self.rbac_role = RBACRole.objects.filter(name=target).first()
            except Exception:
                # RBACRole table may not exist yet during early migrations; the
                # seed/data migration backfills existing users afterwards.
                pass

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


class RBACRole(TimestampedModel):
    """A role as data (RBAC Track 2): a named set of capability strings.

    Capability identity is code-defined (``apps.core.capabilities.ALL_CAPABILITIES``);
    ``capabilities`` is stored as a JSON list and validated to be a subset of it
    (a JSONField is simpler to query for membership than an M2M lookup table and
    keeps capability identity in code). ``is_system`` marks the seeded roles;
    ``is_immutable`` marks the superadmin role, which can never be deleted or
    down-scoped below the full capability set.
    """
    name = models.CharField(max_length=64, unique=True)
    description = models.TextField(blank=True)
    capabilities = models.JSONField(default=list)
    is_system = models.BooleanField(default=False)
    is_immutable = models.BooleanField(default=False)

    class Meta(TimestampedModel.Meta):
        verbose_name = "RBAC Role"
        verbose_name_plural = "RBAC Roles"
        ordering = ["name"]

    def __str__(self):
        return self.name

    def capability_set(self) -> set[str]:
        """The role's capabilities as a set (for membership tests)."""
        return set(self.capabilities or [])

    def clean(self):
        from django.core.exceptions import ValidationError

        from .capabilities import ALL_CAPABILITIES
        unknown = self.capability_set() - ALL_CAPABILITIES
        if unknown:
            raise ValidationError(
                {"capabilities": f"Unknown capabilities: {sorted(unknown)}"})

    def save(self, *args, **kwargs):
        from django.core.exceptions import ValidationError

        from .capabilities import ALL_CAPABILITIES
        self.clean()  # reject any capability not in the code catalog
        # The immutable (superadmin) role can never be down-scoped.
        if self.is_immutable and not ALL_CAPABILITIES.issubset(self.capability_set()):
            raise ValidationError("The superadmin role cannot be down-scoped.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        from django.core.exceptions import ValidationError
        if self.is_immutable:
            raise ValidationError("The superadmin role cannot be deleted.")
        return super().delete(*args, **kwargs)


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

    class TemperatureUnit(models.TextChoices):
        CELSIUS    = "C", "Celsius (°C)"
        FAHRENHEIT = "F", "Fahrenheit (°F)"

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
    # Temperatures are stored/returned in Celsius; this only controls display.
    temperature_unit = models.CharField(
        max_length=1, choices=TemperatureUnit.choices, default=TemperatureUnit.CELSIUS)

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
        # Topology
        TOPOLOGY_LINK_CREATED = "topology_link_created", "Manual Topology Link Created"
        TOPOLOGY_LINK_UPDATED = "topology_link_updated", "Manual Topology Link Updated"
        TOPOLOGY_LINK_DELETED = "topology_link_deleted", "Manual Topology Link Deleted"
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
        MIST_SYNC = "mist_sync", "Mist Sync"
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
        ALERT_RULE_DELETED = "alert_rule_deleted", "Alert Rule Deleted"
        # Sites
        SITE_CREATED = "site_created", "Site Created"
        SITE_UPDATED = "site_updated", "Site Updated"
        SITE_DELETED = "site_deleted", "Site Deleted"
        # Agents
        AGENT_ENROLLED = "agent_enrolled", "Agent Enrolled"
        AGENT_REVOKED = "agent_revoked", "Agent Revoked"
        AGENT_SITE_CHANGED = "agent_site_changed", "Agent Site Changed"
        # ChatOps
        CHATOPS_QUERY = "chatops_query", "ChatOps Query"
        CHATOPS_DENIED = "chatops_denied", "ChatOps Query Denied"
        # Multi-factor auth (TOTP)
        MFA_ENABLED = "mfa_enabled", "MFA Enabled"
        MFA_DISABLED = "mfa_disabled", "MFA Disabled"
        MFA_FAILED = "mfa_failed", "MFA Verification Failed"
        MFA_RESET_BY_ADMIN = "mfa_reset_by_admin", "MFA Reset by Admin"
        MFA_ENROLLMENT_FORCED = "mfa_enrollment_forced", "MFA Enrollment Forced"
        MFA_ENROLLMENT_COMPLETED = "mfa_enrollment_completed", "MFA Enrollment Completed"

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


class MFADevice(TimestampedModel):
    """A user's TOTP authenticator (RFC 6238), one per user.

    The TOTP secret is a credential: in OpenBao-configured deployments it lives
    in OpenBao at ``netpulse/mfa/{user_id}`` and ``secret_encrypted`` stays empty;
    otherwise ``secret_encrypted`` holds a Fernet-encrypted copy. Never plaintext
    in the DB, never returned by the API, never logged. Recovery codes are stored
    hashed (PBKDF2) and are single-use. See apps.core.mfa.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="mfa_device")
    mfa_enabled = models.BooleanField(default=False)
    # Fernet-encrypted TOTP secret — only populated when OpenBao is unconfigured
    # (dev/test). In production the secret is in OpenBao and this stays blank.
    secret_encrypted = models.TextField(blank=True, default="")
    confirmed_at = models.DateTimeField(null=True, blank=True)
    # TOTP time-step of the last accepted code — replay guard (a code can't be
    # reused within its validity window).
    last_step = models.BigIntegerField(null=True, blank=True)
    # [{"hash": <pbkdf2>, "used_at": <iso|None>}] — hashed, single-use.
    recovery_codes = models.JSONField(default=list, blank=True)

    def __str__(self):
        return f"MFADevice<{self.user_id} enabled={self.mfa_enabled}>"

    def _vault_path(self) -> str:
        return f"netpulse/mfa/{self.user_id}"

    # ── secret (OpenBao-primary, encrypted-DB fallback) ────────────────────
    def set_secret(self, plaintext: str) -> None:
        from apps.credentials import vault

        from .mfa import encrypt_secret

        if vault.vault_enabled():
            vault.write_secret(self._vault_path(), {"totp_secret": plaintext})
            self.secret_encrypted = ""
        else:
            self.secret_encrypted = encrypt_secret(plaintext)

    def get_secret(self) -> str:
        if self.secret_encrypted:
            from .mfa import decrypt_secret

            try:
                return decrypt_secret(self.secret_encrypted)
            except Exception:
                return ""
        from apps.credentials import vault

        if vault.vault_enabled():
            return vault.read_secret(self._vault_path()).get("totp_secret", "")
        return ""

    def clear(self) -> None:
        """Reset to no-MFA and remove the secret from OpenBao (break-glass /
        admin reset / user-disable). Caller saves."""
        from apps.credentials import vault

        self.mfa_enabled = False
        self.secret_encrypted = ""
        self.confirmed_at = None
        self.last_step = None
        self.recovery_codes = []
        try:
            if vault.vault_enabled():
                vault.delete_secret(self._vault_path())
        except Exception:
            pass

    # ── verification ───────────────────────────────────────────────────────
    def verify_totp(self, code: str, *, record: bool = True, for_time=None) -> bool:
        """Verify a TOTP code with skew window + replay guard.

        When ``record`` (the default, used at the login second factor and on
        disable), a valid code's time-step is remembered and any step ``<=`` it is
        rejected as a replay; mutates ``last_step`` so the caller must save. The
        confirm step passes ``record=False`` so it doesn't consume the step the
        user will immediately log in with.
        """
        from . import mfa as mfamod

        secret = self.get_secret()
        if not secret:
            return False
        step = mfamod.matching_step(secret, code, for_time)
        if step is None:
            return False
        if record:
            if self.last_step is not None and step <= self.last_step:
                return False  # replay of an already-consumed step
            self.last_step = step
        return True

    def verify_recovery(self, code: str) -> bool:
        """Consume a single-use recovery code. Mutates ``recovery_codes``; caller
        saves."""
        from django.utils import timezone

        from . import mfa as mfamod

        for entry in self.recovery_codes:
            if entry.get("used_at"):
                continue
            if mfamod.verify_recovery_code(code, entry.get("hash", "")):
                entry["used_at"] = timezone.now().isoformat()
                return True
        return False

    def set_recovery_codes(self, plaintext_codes: list[str]) -> None:
        from . import mfa as mfamod

        self.recovery_codes = [
            {"hash": mfamod.hash_recovery_code(c), "used_at": None} for c in plaintext_codes
        ]

    @property
    def recovery_codes_remaining(self) -> int:
        return sum(1 for e in self.recovery_codes if not e.get("used_at"))
