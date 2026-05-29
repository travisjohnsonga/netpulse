from django.contrib.auth import get_user_model
from django.db import models

from apps.core.models import TimestampedModel


class Site(TimestampedModel):
    name = models.CharField(max_length=255, unique=True)
    location = models.CharField(max_length=255, blank=True)
    description = models.TextField(blank=True)

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
        MAINTENANCE = "maintenance", "Maintenance"
        DECOMMISSIONED = "decommissioned", "Decommissioned"

    class Platform(models.TextChoices):
        IOS = "ios", "Cisco IOS"
        IOS_XE = "ios_xe", "Cisco IOS-XE"
        IOS_XR = "ios_xr", "Cisco IOS-XR"
        NXOS = "nxos", "Cisco NX-OS"
        EOS = "eos", "Arista EOS"
        JUNOS = "junos", "Juniper JunOS"
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
    site = models.ForeignKey(Site, null=True, blank=True, on_delete=models.SET_NULL, related_name="devices")
    groups = models.ManyToManyField(DeviceGroup, blank=True, related_name="devices")
    # Credentials are managed via CredentialProfile records (secrets in OpenBao);
    # the through model records the purpose + per-device usage stats.
    credentials = models.ManyToManyField(
        "credentials.CredentialProfile",
        through="credentials.DeviceCredential",
        related_name="devices",
        blank=True,
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
