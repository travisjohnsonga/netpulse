"""
Telemetry collection configuration.

Time-series telemetry data itself lives in InfluxDB; these models hold the
*configuration* of what to collect per device: device-level metric toggles
(TelemetryConfig) and the set of interfaces to poll (MonitoredInterface).
"""
from django.contrib.auth import get_user_model
from django.db import models

from apps.core.models import TimestampedModel
from apps.devices.models import Device


class TelemetryConfig(TimestampedModel):
    class Method(models.TextChoices):
        SNMP = "snmp", "SNMP"
        GNMI = "gnmi", "gNMI"
        BOTH = "both", "Both"

    device = models.OneToOneField(Device, on_delete=models.CASCADE, related_name="telemetry_config")
    primary_method = models.CharField(max_length=8, choices=Method.choices, default=Method.SNMP)
    snmp_interval = models.IntegerField(default=300)
    gnmi_interval = models.IntegerField(default=30)

    collect_cpu = models.BooleanField(default=True)
    collect_memory = models.BooleanField(default=True)
    collect_temperature = models.BooleanField(default=True)
    collect_power = models.BooleanField(default=True)
    collect_fans = models.BooleanField(default=True)
    collect_bgp = models.BooleanField(default=True)
    collect_inventory = models.BooleanField(default=True)
    collect_lldp = models.BooleanField(default=True)

    # Per-device polling-interval overrides (null → use the global default).
    override_intervals = models.BooleanField(default=False)
    device_metrics_interval = models.IntegerField(null=True, blank=True)
    interface_traffic_interval = models.IntegerField(null=True, blank=True)
    interface_status_interval = models.IntegerField(null=True, blank=True)
    bgp_interval = models.IntegerField(null=True, blank=True)

    def __str__(self):
        return f"TelemetryConfig({self.device.hostname})"


class SNMPGlobalSettings(TimestampedModel):
    """Singleton (pk=1): global SNMP polling intervals + session parameters."""

    device_metrics_interval = models.IntegerField(default=300)
    interface_traffic_interval = models.IntegerField(default=300)
    interface_status_interval = models.IntegerField(default=60)
    bgp_interval = models.IntegerField(default=60)
    inventory_interval = models.IntegerField(default=3600)
    lldp_interval = models.IntegerField(default=3600)

    max_concurrent_sessions = models.IntegerField(default=10)
    snmp_timeout = models.IntegerField(default=5)
    snmp_retries = models.IntegerField(default=3)
    bulk_get_enabled = models.BooleanField(default=True)
    bulk_get_max_repetitions = models.IntegerField(default=25)

    class Meta:
        verbose_name = "SNMP global settings"
        verbose_name_plural = "SNMP global settings"

    def __str__(self):
        return "SNMP global settings"

    @classmethod
    def load(cls) -> "SNMPGlobalSettings":
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class MonitoredInterface(TimestampedModel):
    class CollectionMethod(models.TextChoices):
        AUTO = "auto", "Auto"
        SNMP = "snmp", "SNMP"
        GNMI = "gnmi", "gNMI"
        REST = "rest", "REST API"  # AOS-CX REST interface discovery

    device = models.ForeignKey(Device, on_delete=models.CASCADE, related_name="monitored_interfaces")
    if_index = models.IntegerField(null=True, blank=True)
    if_name = models.CharField(max_length=255)
    if_description = models.CharField(max_length=255, blank=True)
    if_speed_mbps = models.IntegerField(null=True, blank=True)
    if_type = models.CharField(max_length=64, blank=True)

    lldp_neighbor_hostname = models.CharField(max_length=255, null=True, blank=True)
    lldp_neighbor_port = models.CharField(max_length=255, null=True, blank=True)
    lldp_neighbor_desc = models.CharField(max_length=255, null=True, blank=True)

    poll_traffic = models.BooleanField(default=True)
    poll_errors = models.BooleanField(default=True)
    poll_status = models.BooleanField(default=True)
    collection_method = models.CharField(max_length=8, choices=CollectionMethod.choices, default=CollectionMethod.AUTO)

    # NOTE: circuit_override FK omitted until a CircuitOverride model exists.
    last_discovered = models.DateTimeField(null=True, blank=True)
    last_status = models.CharField(max_length=16, default="unknown")
    last_status_changed = models.DateTimeField(null=True, blank=True)

    # State-change alerting
    class AlertSeverity(models.TextChoices):
        CRITICAL = "critical", "Critical"
        HIGH = "high", "High"
        MEDIUM = "medium", "Medium"
        LOW = "low", "Low"

    alert_on_down = models.BooleanField(default=True)
    alert_on_up = models.BooleanField(default=True)  # notify recovery too
    alert_severity = models.CharField(max_length=10, choices=AlertSeverity.choices, default=AlertSeverity.HIGH)
    # Require N consecutive down polls before alerting (flap suppression).
    consecutive_polls_before_alert = models.IntegerField(default=1)

    class Meta(TimestampedModel.Meta):
        unique_together = ["device", "if_name"]
        indexes = [models.Index(fields=["device", "if_name"])]

    def __str__(self):
        return f"{self.device.hostname}:{self.if_name}"


class ConfigPush(TimestampedModel):
    """Audit record of a telemetry-config push to a device."""

    device = models.ForeignKey(Device, on_delete=models.CASCADE, related_name="config_pushes")
    pushed_by = models.ForeignKey(
        get_user_model(), null=True, blank=True,
        on_delete=models.SET_NULL, related_name="config_pushes",
    )
    sections = models.JSONField(default=list)
    success = models.BooleanField(default=False)
    output = models.TextField(blank=True)
    errors = models.JSONField(default=list)

    class Meta(TimestampedModel.Meta):
        indexes = [models.Index(fields=["device", "-created_at"])]

    def __str__(self):
        return f"push {self.device.hostname} {self.sections} ({'ok' if self.success else 'fail'})"
