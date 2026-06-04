"""
ARP and MAC address-table entries collected from network devices over SSH.

Relational (PostgreSQL), not time-series: these are current-state tables that
change slowly and are searched by IP/MAC — InfluxDB is the wrong store. Each
collection upserts ARP and replaces the device's MAC table.
"""
from __future__ import annotations

from django.db import models

from apps.devices.models import Device


class ARPEntry(models.Model):
    device = models.ForeignKey(Device, on_delete=models.CASCADE, related_name="arp_entries")
    ip_address = models.GenericIPAddressField()
    mac_address = models.CharField(max_length=17)          # normalized aa:bb:cc:dd:ee:ff
    interface = models.CharField(max_length=64, blank=True)
    vlan = models.IntegerField(null=True, blank=True)
    protocol = models.CharField(max_length=20, default="Internet")
    entry_type = models.CharField(max_length=20, default="dynamic", blank=True)  # dynamic/static
    age_minutes = models.IntegerField(null=True, blank=True)
    collected_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ["device", "ip_address"]
        indexes = [
            models.Index(fields=["mac_address"]),
            models.Index(fields=["ip_address"]),
            models.Index(fields=["device", "collected_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.ip_address} → {self.mac_address} ({self.device_id})"


class MACEntry(models.Model):
    device = models.ForeignKey(Device, on_delete=models.CASCADE, related_name="mac_entries")
    mac_address = models.CharField(max_length=17)          # normalized
    vlan = models.IntegerField(null=True, blank=True)
    interface = models.CharField(max_length=64, blank=True)
    entry_type = models.CharField(max_length=20, default="dynamic")  # dynamic/static
    collected_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ["device", "mac_address", "vlan"]
        indexes = [
            models.Index(fields=["mac_address"]),
            models.Index(fields=["device", "vlan"]),
            models.Index(fields=["collected_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.mac_address} vlan={self.vlan} port={self.interface} ({self.device_id})"


class MACVendor(models.Model):
    """OUI prefix → vendor lookup cache (populated from the IEEE OUI registry)."""
    oui = models.CharField(max_length=8, primary_key=True)   # "aa:bb:cc"
    vendor = models.CharField(max_length=128)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"{self.oui} → {self.vendor}"
