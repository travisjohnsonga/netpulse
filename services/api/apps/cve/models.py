from django.db import models

from apps.core.models import TimestampedModel
from apps.devices.models import Device


class CVE(TimestampedModel):
    class Severity(models.TextChoices):
        CRITICAL = "critical", "Critical"
        HIGH = "high", "High"
        MEDIUM = "medium", "Medium"
        LOW = "low", "Low"
        NONE = "none", "None"

    cve_id = models.CharField(max_length=30, unique=True, db_index=True)
    description = models.TextField()
    severity = models.CharField(max_length=10, choices=Severity.choices, db_index=True)
    cvss_score = models.DecimalField(max_digits=4, decimal_places=1, null=True)
    cvss_vector = models.CharField(max_length=255, blank=True)
    published_at = models.DateTimeField(null=True)
    modified_at = models.DateTimeField(null=True)
    source_url = models.URLField(blank=True)

    def __str__(self):
        return self.cve_id


class DeviceCVE(TimestampedModel):
    device = models.ForeignKey(Device, on_delete=models.CASCADE, related_name="cves")
    cve = models.ForeignKey(CVE, on_delete=models.CASCADE, related_name="affected_devices")
    is_patched = models.BooleanField(default=False, db_index=True)
    patched_at = models.DateTimeField(null=True, blank=True)

    class Meta(TimestampedModel.Meta):
        unique_together = [("device", "cve")]
        indexes = [models.Index(fields=["device", "is_patched"])]
