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

    # Source feed this CVE was ingested from (nvd / cisco_psirt / community).
    source = models.CharField(max_length=20, default="nvd", db_index=True)
    # On the CISA Known-Exploited-Vulnerabilities list — highest priority.
    cisa_kev = models.BooleanField(default=False, db_index=True)
    # NetPulse platform keys this CVE is known to affect (e.g. ["ios_xe"]).
    affected_platforms = models.JSONField(default=list, blank=True)
    # Extracted CPE match criteria used for version matching. Each entry:
    # {platform, product, version_start_including, version_start_excluding,
    #  version_end_including, version_end_excluding, exact_version}.
    cpe_configs = models.JSONField(default=list, blank=True)
    # Trimmed raw NVD/PSIRT record for audit/debugging.
    raw_data = models.JSONField(default=dict, blank=True)

    def __str__(self):
        return self.cve_id


class DeviceCVE(TimestampedModel):
    class MatchType(models.TextChoices):
        EXACT_VERSION = "exact_version", "Exact version"
        VERSION_RANGE = "version_range", "Version range"
        KEYWORD = "keyword", "Keyword / platform"
        UNVERIFIED = "unverified", "Unverified (version unknown)"

    device = models.ForeignKey(Device, on_delete=models.CASCADE, related_name="cves")
    cve = models.ForeignKey(CVE, on_delete=models.CASCADE, related_name="affected_devices")
    is_patched = models.BooleanField(default=False, db_index=True)
    patched_at = models.DateTimeField(null=True, blank=True)
    # How confidently this CVE was correlated to the device.
    match_type = models.CharField(
        max_length=16, choices=MatchType.choices, default=MatchType.KEYWORD, db_index=True,
    )
    match_detail = models.CharField(max_length=255, blank=True)
    notes = models.TextField(blank=True)

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

    # Last-sync telemetry (surfaced on the CVE page; never secret).
    last_synced_at = models.DateTimeField(null=True, blank=True)
    last_sync_status = models.CharField(max_length=20, blank=True)  # ok / error / running
    last_sync_summary = models.JSONField(default=dict, blank=True)

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
