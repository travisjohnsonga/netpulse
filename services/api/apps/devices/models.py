from django.contrib.auth import get_user_model
from django.db import models

from apps.core.models import TimestampedModel


class Site(TimestampedModel):
    class SiteType(models.TextChoices):
        DATACENTER = "datacenter", "Datacenter"
        CAMPUS = "campus", "Campus"
        BRANCH = "branch", "Branch"
        REMOTE = "remote", "Remote"
        CLOUD = "cloud", "Cloud"

    name = models.CharField(max_length=255, unique=True)
    slug = models.SlugField(max_length=255, unique=True, blank=True)
    description = models.TextField(blank=True)
    # Kept for backwards compatibility with existing data/UX.
    location = models.CharField(max_length=255, blank=True)

    site_type = models.CharField(max_length=20, choices=SiteType.choices, default=SiteType.BRANCH, db_index=True)

    # Address / geo
    address = models.CharField(max_length=255, blank=True)
    city = models.CharField(max_length=120, blank=True)
    state = models.CharField(max_length=120, blank=True)
    country = models.CharField(max_length=120, blank=True)
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)

    # Hierarchy (parent/child sites). tenant FK omitted until multi-tenancy lands.
    parent_site = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL, related_name="child_sites"
    )

    # Contact
    contact_name = models.CharField(max_length=255, blank=True)
    contact_email = models.EmailField(blank=True)
    contact_phone = models.CharField(max_length=64, blank=True)

    # Collector that devices at this site default to (when a device has no
    # collector of its own).
    default_collector = models.ForeignKey(
        "collectors.Collector",
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="default_for_sites",
    )

    notes = models.TextField(blank=True)

    def save(self, *args, **kwargs):
        if not self.slug:
            from django.utils.text import slugify
            base = slugify(self.name) or "site"
            slug, n = base, 1
            while Site.objects.exclude(pk=self.pk).filter(slug=slug).exists():
                n += 1
                slug = f"{base}-{n}"
            self.slug = slug
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class DeviceGroup(TimestampedModel):
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)

    def __str__(self):
        return self.name


class Device(TimestampedModel):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        INACTIVE = "inactive", "Inactive"
        UNREACHABLE = "unreachable", "Unreachable"
        MAINTENANCE = "maintenance", "Maintenance"
        DECOMMISSIONED = "decommissioned", "Decommissioned"

    class Platform(models.TextChoices):
        IOS = "ios", "Cisco IOS"
        IOS_XE = "ios_xe", "Cisco IOS-XE"
        IOS_XR = "ios_xr", "Cisco IOS-XR"
        NXOS = "nxos", "Cisco NX-OS"
        EOS = "eos", "Arista EOS"
        JUNOS = "junos", "Juniper JunOS"
        FORTIOS = "fortios", "Fortinet FortiOS"
        PANOS = "panos", "Palo Alto PAN-OS"
        SONIC = "sonic", "SONiC"
        OTHER = "other", "Other"

    hostname = models.CharField(max_length=255, unique=True, db_index=True)
    ip_address = models.GenericIPAddressField(unique=True, db_index=True)
    management_ip = models.GenericIPAddressField(null=True, blank=True)
    vendor = models.CharField(max_length=100, blank=True)
    model = models.CharField(max_length=100, blank=True)
    platform = models.CharField(max_length=20, choices=Platform.choices, default=Platform.OTHER)
    os_version = models.CharField(max_length=100, blank=True)
    serial_number = models.CharField(max_length=100, blank=True, db_index=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE, db_index=True)
    # Last time NetPulse successfully reached the device (e.g. config collection).
    last_seen = models.DateTimeField(null=True, blank=True, db_index=True)
    # Liveness/reachability (updated by run_reachability_monitor).
    is_reachable = models.BooleanField(default=True)
    last_reachability_check = models.DateTimeField(null=True, blank=True)
    reachability_method = models.CharField(max_length=8, blank=True)  # ping/tcp/snmp
    consecutive_failures = models.IntegerField(default=0)
    # When the device first transitioned to 'unreachable' (cleared on recovery).
    # Drives the downtime duration shown in the device-list status badge.
    unreachable_since = models.DateTimeField(null=True, blank=True)
    site = models.ForeignKey(Site, null=True, blank=True, on_delete=models.SET_NULL, related_name="devices")
    groups = models.ManyToManyField(DeviceGroup, blank=True, related_name="devices")
    # A device uses one multi-protocol credential profile (secrets in OpenBao).
    credential_profile = models.ForeignKey(
        "credentials.CredentialProfile",
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="devices",
    )
    # Poller/collector that monitors this device. When unset, falls back to the
    # device's site default collector, then the global default collector.
    collector = models.ForeignKey(
        "collectors.Collector",
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="devices",
    )
    notes = models.TextField(blank=True)

    class Meta(TimestampedModel.Meta):
        pass

    def __str__(self):
        return self.hostname


class DiscoveryJob(TimestampedModel):
    class Method(models.TextChoices):
        PASSIVE   = "passive",   "Passive (ingest source IPs)"
        TOPOLOGY  = "topology",  "Topology Walk (CDP/LLDP/route table)"
        SCAN      = "scan",      "Active Scan (SNMP/SSH probe)"
        IMPORT    = "import",    "Import (NetBox/CSV)"

    class Status(models.TextChoices):
        PENDING   = "pending",   "Pending"
        RUNNING   = "running",   "Running"
        COMPLETED = "completed", "Completed"
        FAILED    = "failed",    "Failed"
        CANCELLED = "cancelled", "Cancelled"

    name        = models.CharField(max_length=255)
    method      = models.CharField(max_length=20, choices=Method.choices, db_index=True)
    status      = models.CharField(max_length=20, choices=Status.choices,
                                   default=Status.PENDING, db_index=True)
    # Subnets to probe (list of CIDR strings) — stored as JSON
    subnets           = models.JSONField(default=list)
    allowed_subnets   = models.JSONField(default=list)
    excluded_subnets  = models.JSONField(default=list)
    # Seed device for topology walk
    seed_device       = models.ForeignKey(Device, null=True, blank=True,
                                          on_delete=models.SET_NULL,
                                          related_name="seeded_discovery_jobs")
    max_depth         = models.PositiveIntegerField(default=10)
    max_devices       = models.PositiveIntegerField(default=1000)
    rate_limit_pps    = models.PositiveIntegerField(default=10)
    devices_found     = models.PositiveIntegerField(default=0)
    started_at        = models.DateTimeField(null=True, blank=True)
    completed_at      = models.DateTimeField(null=True, blank=True)
    error_message     = models.TextField(blank=True)
    created_by        = models.ForeignKey(
        get_user_model(), null=True, blank=True,
        on_delete=models.SET_NULL, related_name="discovery_jobs"
    )

    class Meta(TimestampedModel.Meta):
        indexes = [models.Index(fields=["status", "-created_at"])]

    def __str__(self):
        return f"{self.name} ({self.method}/{self.status})"


class DiscoveredDevice(TimestampedModel):
    class Status(models.TextChoices):
        PENDING  = "pending",  "Pending approval"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    job                  = models.ForeignKey(DiscoveryJob, on_delete=models.CASCADE,
                                              related_name="discovered_devices")
    source_ip            = models.GenericIPAddressField(db_index=True)
    # Which detection methods responded — list of strings
    detection_methods    = models.JSONField(default=list)
    # Which protocols answered — e.g. {"snmp": true, "gnmi": false, "ssh": true}
    responds_to          = models.JSONField(default=dict)
    confidence_score     = models.PositiveSmallIntegerField(default=0)  # 0-100
    discovered_hostname  = models.CharField(max_length=255, blank=True)
    discovered_vendor    = models.CharField(max_length=100, blank=True)
    discovered_platform  = models.CharField(max_length=100, blank=True)
    discovered_model     = models.CharField(max_length=100, blank=True)
    discovered_os        = models.CharField(max_length=100, blank=True)
    # Raw sysDescr / banner / API response
    raw_fingerprint      = models.TextField(blank=True)
    status               = models.CharField(max_length=20, choices=Status.choices,
                                             default=Status.PENDING, db_index=True)
    # Set when admin approves — links to the created Device record
    approved_device      = models.ForeignKey(Device, null=True, blank=True,
                                              on_delete=models.SET_NULL,
                                              related_name="discovery_sources")
    approved_by          = models.ForeignKey(
        get_user_model(), null=True, blank=True,
        on_delete=models.SET_NULL, related_name="approved_discoveries"
    )
    approved_at          = models.DateTimeField(null=True, blank=True)

    class Meta(TimestampedModel.Meta):
        unique_together = [("job", "source_ip")]
        indexes = [
            models.Index(fields=["status", "-confidence_score"]),
            models.Index(fields=["source_ip"]),
        ]

    def __str__(self):
        return f"{self.source_ip} ({self.status}, score={self.confidence_score})"


class TopologyLink(TimestampedModel):
    """A discovered link between two devices (e.g. via LLDP)."""

    device_a = models.ForeignKey(Device, on_delete=models.CASCADE, related_name="links_as_a")
    port_a = models.CharField(max_length=255)
    device_b = models.ForeignKey(Device, on_delete=models.CASCADE, related_name="links_as_b")
    port_b = models.CharField(max_length=255, blank=True)
    discovered_via = models.CharField(max_length=20, default="lldp")
    link_speed_mbps = models.IntegerField(null=True, blank=True)
    last_seen = models.DateTimeField(null=True, blank=True)

    class Meta(TimestampedModel.Meta):
        # Links are stored canonically (lower device id = device_a), so a single
        # constraint on the full tuple dedupes both same-direction repeats AND
        # the two directions of one physical link.
        constraints = [
            models.UniqueConstraint(
                fields=["device_a", "port_a", "device_b", "port_b"],
                name="unique_topology_link",
            ),
        ]
        indexes = [models.Index(fields=["device_a"]), models.Index(fields=["device_b"])]

    def __str__(self):
        return f"{self.device_a.hostname}:{self.port_a} ↔ {self.device_b.hostname}:{self.port_b}"
