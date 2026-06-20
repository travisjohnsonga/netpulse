"""WAN circuit inventory — provider/bandwidth/IP details tied to an interface."""
from django.db import models

from apps.core.models import TimestampedModel


class WanCircuit(TimestampedModel):
    """A WAN circuit (Internet/MPLS/etc.) optionally bound to a device interface.

    When ``device`` + ``interface`` are set, utilization is derived from that
    interface's InfluxDB throughput vs the circuit bandwidth.
    """

    class CircuitType(models.TextChoices):
        MPLS = "mpls", "MPLS"
        INTERNET = "internet", "Internet"
        DIA = "dia", "Dedicated Internet Access"
        BROADBAND = "broadband", "Broadband"
        FIBER = "fiber", "Fiber"
        COAX = "coax", "Coax/Cable"
        LTE = "lte", "LTE/Cellular"
        SD_WAN = "sdwan", "SD-WAN"
        DARK_FIBER = "dark_fiber", "Dark Fiber"
        POINT_TO_POINT = "p2p", "Point-to-Point"
        OTHER = "other", "Other"

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        INACTIVE = "inactive", "Inactive"
        PENDING = "pending", "Pending Install"
        CANCELLED = "cancelled", "Cancelled"

    # Identity
    name = models.CharField(max_length=128, help_text='e.g. "WCO2 Primary Internet"')
    circuit_id = models.CharField(max_length=128, blank=True, help_text="Provider circuit ID")
    circuit_type = models.CharField(max_length=32, choices=CircuitType.choices, default=CircuitType.INTERNET)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)

    # Provider
    provider = models.CharField(max_length=128, blank=True, help_text="e.g. AT&T, Comcast, Lumen")
    provider_account = models.CharField(max_length=128, blank=True)
    contract_end_date = models.DateField(null=True, blank=True)
    monthly_cost = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)

    # Bandwidth
    bandwidth_mbps_download = models.IntegerField(null=True, blank=True, help_text="Download/ingress Mbps")
    bandwidth_mbps_upload = models.IntegerField(
        null=True, blank=True, help_text="Upload/egress Mbps. Blank = same as download")
    committed_mbps = models.IntegerField(null=True, blank=True, help_text="CIR - Committed Information Rate")

    # ISP IP assignment
    isp_ipv4_block = models.CharField(max_length=64, blank=True, help_text="e.g. 203.0.113.0/30")
    isp_ipv6_block = models.CharField(max_length=128, blank=True, help_text="e.g. 2001:db8::/48")
    gateway_ip = models.GenericIPAddressField(null=True, blank=True, help_text="ISP gateway/next-hop IP")
    usable_ips = models.CharField(max_length=128, blank=True, help_text="e.g. 203.0.113.1 - 203.0.113.2")
    bgp_asn = models.CharField(max_length=20, blank=True, help_text="ISP BGP ASN if applicable")
    our_bgp_asn = models.CharField(max_length=20, blank=True, help_text="Our BGP ASN if applicable")

    # Interface binding
    device = models.ForeignKey(
        "devices.Device", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="wan_circuits", help_text="Router/firewall this circuit terminates on")
    interface = models.CharField(max_length=64, blank=True, help_text="Interface name, e.g. GigabitEthernet0/0/1")
    ip_address = models.GenericIPAddressField(null=True, blank=True, help_text="WAN IP on this circuit")

    # Site
    site = models.ForeignKey(
        "devices.Site", on_delete=models.SET_NULL, null=True, blank=True, related_name="wan_circuits")

    # Thresholds
    alert_threshold_pct = models.IntegerField(default=80, help_text="Alert when utilization exceeds this %")

    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["site__name", "name"]

    def __str__(self):
        return f"{self.name} ({self.provider})" if self.provider else self.name

    @property
    def bandwidth_mbps(self) -> int | None:
        """Download (symmetric base)."""
        return self.bandwidth_mbps_download

    @property
    def upload_mbps(self) -> int | None:
        """Upload bandwidth, falling back to download when symmetric."""
        return self.bandwidth_mbps_upload or self.bandwidth_mbps_download
