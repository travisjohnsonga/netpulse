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
    # Credential secret is stored in OpenBao; only the path is persisted here.
    credential_path = models.CharField(max_length=512, blank=True)
    notes = models.TextField(blank=True)

    class Meta(TimestampedModel.Meta):
        pass

    def __str__(self):
        return self.hostname
