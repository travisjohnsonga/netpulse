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


class UnifiController(TimestampedModel):
    """
    A Ubiquiti UniFi controller (one per site is common). NetPulse polls each
    enabled controller to import its managed devices (APs/switches/gateways).
    The controller password lives in OpenBao at ``vault_path`` — never the DB.
    """
    name = models.CharField(max_length=128)
    # Site Manager (cloud) host id, when this controller was auto-discovered
    # from a UnifiCloudAccount. Blank for manually-added controllers.
    cloud_host_id = models.CharField(max_length=128, blank=True, db_index=True)
    host = models.CharField(max_length=255, help_text="Controller hostname or IP")
    port = models.IntegerField(default=8443)
    # Legacy: credentials now come from credential_profile (see below). Kept
    # nullable so the data migration can seed a profile from existing values.
    username = models.CharField(max_length=128, blank=True)
    # Local controller API credentials. The profile should have HTTPS (or SSH)
    # enabled; the username/password must match a UniFi local admin account.
    credential_profile = models.ForeignKey(
        "credentials.CredentialProfile", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="unifi_controllers",
        help_text="Credential profile for local controller API access "
                  "(HTTPS credentials recommended).",
    )
    verify_ssl = models.BooleanField(default=False)
    # UniFi site identifier on the controller (UniFi → Settings → System → Site
    # ID). Named unifi_site_id to avoid clashing with the `site` FK's site_id.
    unifi_site_id = models.CharField(max_length=64, default="default",
                                     help_text="UniFi site ID (default=default)")
    # Optional NetPulse Site that imported devices are assigned to.
    site = models.ForeignKey(
        "devices.Site", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="unifi_controllers",
    )
    enabled = models.BooleanField(default=True)
    last_sync = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=512, blank=True)
    device_count = models.IntegerField(default=0)
    # Hardware model / firmware version, populated from cloud discovery.
    model = models.CharField(max_length=128, blank=True)
    version = models.CharField(max_length=64, blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.host})"

    @property
    def vault_path(self) -> str:
        """OpenBao path holding this controller's password (key 'password')."""
        return f"netpulse/integrations/unifi/{self.id}"


# OpenBao path holding the UniFi Site Manager (cloud) API key (key "api_key").
UNIFI_CLOUD_VAULT_PATH = "netpulse/integrations/unifi/cloud"


class UnifiCloudAccount(TimestampedModel):
    """
    Singleton UniFi Site Manager (cloud) account. One API key auto-discovers all
    the controllers/hosts on the UI.com account; the key lives in OpenBao at
    UNIFI_CLOUD_VAULT_PATH. Use ``load()`` to fetch the single row.
    """
    name = models.CharField(max_length=128, default="UniFi Cloud Account")
    enabled = models.BooleanField(default=True)
    last_sync = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=512, blank=True)
    host_count = models.IntegerField(default=0)

    class Meta:
        verbose_name = "UniFi Cloud Account"
        verbose_name_plural = "UniFi Cloud Account"

    def __str__(self):
        return self.name

    @classmethod
    def load(cls) -> "UnifiCloudAccount":
        obj = cls.objects.first()
        if obj is None:
            obj = cls.objects.create()
        return obj


class UnifiApStatus(TimestampedModel):
    """
    Latest telemetry snapshot for a UniFi access point, refreshed every cycle by
    the scheduler's UniFi-telemetry task (see apps.integrations.unifi_telemetry).

    UniFi APs are controller-managed (no SSH/SNMP), so their live state is pulled
    from the controller's ``stat/device`` payload. The rolling time-series lives
    in InfluxDB (``unifi_ap_radio`` / ``unifi_ap_health`` measurements); this row
    holds just the most-recent values so the fleet page and device-detail
    Wireless tab can render current state without an InfluxDB round-trip. Per-AP
    radio detail is kept as JSON since its shape (2.4/5/6 GHz) varies by model.
    """
    device = models.OneToOneField(
        "devices.Device", on_delete=models.CASCADE, related_name="unifi_ap_status",
    )
    controller = models.ForeignKey(
        UnifiController, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="ap_statuses",
    )
    # Health / overall.
    state = models.IntegerField(default=0, help_text="UniFi state: 1=connected, 0=disconnected")
    satisfaction = models.IntegerField(null=True, blank=True, help_text="UniFi health score 0-100")
    client_count = models.IntegerField(default=0)
    cpu_pct = models.FloatField(null=True, blank=True)
    memory_pct = models.FloatField(null=True, blank=True)
    temperature_c = models.FloatField(null=True, blank=True)
    uptime_seconds = models.BigIntegerField(null=True, blank=True)
    # Uplink (speed Mbps, type wire|wireless).
    uplink_speed_mbps = models.IntegerField(null=True, blank=True)
    uplink_type = models.CharField(max_length=16, blank=True)
    # Per-radio detail: list of {band, channel, channel_width, tx_power_dbm,
    # noise_floor_dbm, clients, channel_utilization_pct, tx_retries_pct,
    # satisfaction, tx_bytes, rx_bytes}.
    radios = models.JSONField(default=list)
    last_collected = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "UniFi AP Status"
        verbose_name_plural = "UniFi AP Statuses"

    def __str__(self):
        return f"AP status for {self.device_id}"


class UnifiConsoleStatus(TimestampedModel):
    """
    Latest telemetry snapshot for a UniFi console / gateway (UDM, UDM-Pro, Cloud
    Key, UXG …), refreshed by the UniFi-telemetry scheduler task.

    Like UnifiApStatus, this holds the most-recent values so the device-detail
    Overview panels (Controller Status + WAN) render without an InfluxDB
    round-trip; the rolling time-series goes to InfluxDB (unifi_controller_health
    / unifi_wan). WAN detail is JSON since a console may have 1–2 uplinks.
    """
    device = models.OneToOneField(
        "devices.Device", on_delete=models.CASCADE, related_name="unifi_console_status",
    )
    controller = models.ForeignKey(
        UnifiController, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="console_statuses",
    )
    state = models.IntegerField(default=0, help_text="UniFi state: 1=connected, 0=disconnected")
    satisfaction = models.IntegerField(null=True, blank=True, help_text="UniFi health score 0-100")
    cpu_pct = models.FloatField(null=True, blank=True)
    memory_pct = models.FloatField(null=True, blank=True)
    temperature_c = models.FloatField(null=True, blank=True)
    uptime_seconds = models.BigIntegerField(null=True, blank=True)
    loadavg_1 = models.FloatField(null=True, blank=True)
    loadavg_5 = models.FloatField(null=True, blank=True)
    loadavg_15 = models.FloatField(null=True, blank=True)
    num_adopted = models.IntegerField(default=0)
    num_disconnected = models.IntegerField(default=0)
    num_pending = models.IntegerField(default=0)
    # Per-WAN detail: list of {name, key, ip, up, speed_mbps, latency_ms,
    # rx_bps, tx_bps, uptime}.
    wans = models.JSONField(default=list)
    last_collected = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "UniFi Console Status"
        verbose_name_plural = "UniFi Console Statuses"

    def __str__(self):
        return f"Console status for {self.device_id}"
