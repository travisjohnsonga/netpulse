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


class CVEFeedSettings(TimestampedModel):
    """
    Singleton (pk=1) config for the CVE intelligence feeds.

    Feed credentials (NVD API key, Cisco PSIRT OAuth client id/secret, Palo Alto
    API key) live in OpenBao — only the vault path is stored here, never the
    secret. The ``has_*`` properties expose whether a credential is configured
    without revealing it.
    """

    # Feed enable toggles
    nvd_enabled = models.BooleanField(default=True)
    cisa_kev_enabled = models.BooleanField(default=True)
    cisco_psirt_enabled = models.BooleanField(default=False)
    paloalto_enabled = models.BooleanField(default=False)

    # Vault paths (NOT the secrets themselves)
    nvd_api_key_vault_path = models.CharField(max_length=255, blank=True)
    cisco_psirt_client_id_vault_path = models.CharField(max_length=255, blank=True)
    paloalto_api_key_vault_path = models.CharField(max_length=255, blank=True)

    class Meta:
        verbose_name = "CVE feed settings"
        verbose_name_plural = "CVE feed settings"

    def __str__(self):
        return "CVE feed settings"

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    @property
    def has_nvd_api_key(self) -> bool:
        return bool(self.nvd_api_key_vault_path)

    @property
    def has_psirt_credentials(self) -> bool:
        return bool(self.cisco_psirt_client_id_vault_path)

    @property
    def has_paloalto_api_key(self) -> bool:
        return bool(self.paloalto_api_key_vault_path)
