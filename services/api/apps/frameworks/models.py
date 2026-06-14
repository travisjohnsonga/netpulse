"""
Regulatory compliance frameworks.

spane doesn't replace a GRC platform; it maps the operational evidence it
already collects (config compliance, backups, change audit, vuln/EOL, secrets
posture, access control) onto the technical controls auditors ask about, so a
network team can produce an evidence package for SOX / ISO 27001 / NIST CSF /
PCI-DSS / HIPAA / CIS Controls without hand-assembling screenshots.

Each :class:`FrameworkControl` carries a ``mapping_key`` that selects an evidence
collector (see ``apps.frameworks.evidence``); the collector inspects live spane
data and returns a status + evidence. Control catalogs here are *representative
subsets* mapped to the signals spane can actually evidence — not verbatim
reproductions of the full standards.
"""
from django.db import models

from apps.core.models import TimestampedModel


class RegulatoryFramework(TimestampedModel):
    class Key(models.TextChoices):
        SOX = "sox", "SOX (ITGC)"
        ISO_27001 = "iso27001", "ISO/IEC 27001"
        NIST_CSF = "nist_csf", "NIST CSF"
        PCI_DSS = "pci_dss", "PCI-DSS"
        HIPAA = "hipaa", "HIPAA Security Rule"
        CIS = "cis", "CIS Controls v8"

    key = models.CharField(max_length=32, choices=Key.choices, unique=True)
    name = models.CharField(max_length=128)
    description = models.TextField(blank=True)
    version = models.CharField(max_length=32, blank=True)
    enabled = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class FrameworkControl(TimestampedModel):
    framework = models.ForeignKey(
        RegulatoryFramework, on_delete=models.CASCADE, related_name="controls")
    control_id = models.CharField(max_length=64, help_text="e.g. 'PCI-DSS 2.2' or 'CIS 4.1'")
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    category = models.CharField(max_length=128, blank=True)
    # Selects the evidence collector in apps.frameworks.evidence.COLLECTORS.
    mapping_key = models.CharField(max_length=64)
    weight = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ["framework", "control_id"]
        constraints = [
            models.UniqueConstraint(
                fields=["framework", "control_id"], name="uniq_framework_control"),
        ]

    def __str__(self):
        return f"{self.control_id} — {self.title}"
