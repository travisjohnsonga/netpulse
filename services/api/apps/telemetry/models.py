"""
Telemetry collection configuration.

Time-series telemetry data itself lives in InfluxDB; these models hold the
*configuration* of what to collect per device: device-level metric toggles
(TelemetryConfig) and the set of interfaces to poll (MonitoredInterface).
"""
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

    def __str__(self):
        return f"TelemetryConfig({self.device.hostname})"


class MonitoredInterface(TimestampedModel):
    class CollectionMethod(models.TextChoices):
        AUTO = "auto", "Auto"
        SNMP = "snmp", "SNMP"
        GNMI = "gnmi", "gNMI"

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

    class Meta(TimestampedModel.Meta):
        unique_together = ["device", "if_name"]
        indexes = [models.Index(fields=["device", "if_name"])]

    def __str__(self):
        return f"{self.device.hostname}:{self.if_name}"
