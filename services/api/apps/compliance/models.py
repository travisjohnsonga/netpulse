from django.conf import settings
from django.db import models

from apps.core.models import TimestampedModel
from apps.devices.models import Device


class CompliancePolicy(TimestampedModel):
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.name


class CompliancePolicyRule(TimestampedModel):
    class CheckType(models.TextChoices):
        REGEX = "regex", "Regular Expression"
        CONTAINS = "contains", "Contains"
        JMESPATH = "jmespath", "JMESPath"
        NAPALM = "napalm", "NAPALM Getter"

    policy = models.ForeignKey(CompliancePolicy, on_delete=models.CASCADE, related_name="rules")
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    check_type = models.CharField(max_length=20, choices=CheckType.choices)
    check_expression = models.TextField()
    remediation = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.policy.name}: {self.name}"


class ComplianceResult(TimestampedModel):
    class Outcome(models.TextChoices):
        PASS = "pass", "Pass"
        FAIL = "fail", "Fail"
        ERROR = "error", "Error"

    device = models.ForeignKey(Device, on_delete=models.CASCADE, related_name="compliance_results")
    policy = models.ForeignKey(CompliancePolicy, on_delete=models.CASCADE, related_name="results")
    rule = models.ForeignKey(CompliancePolicyRule, on_delete=models.CASCADE, related_name="results")
    outcome = models.CharField(max_length=10, choices=Outcome.choices, db_index=True)
    detail = models.TextField(blank=True)

    class Meta(TimestampedModel.Meta):
        indexes = [models.Index(fields=["device", "policy", "-created_at"])]


# ── Template-based compliance engine ───────────────────────────────────────────
# A second, Jinja2-template-driven compliance model that coexists with the
# policy/rule system above. It renders an expected-config template per
# role/platform/site and diffs it against the device's latest running config,
# classifying deviations as MISSING / EXTRA / DRIFT.
#
# Naming note: the result model is ComplianceTemplateResult (not
# "ComplianceResult") because that name + the device related_name
# "compliance_results" are already taken by the policy system above.


class ComplianceTemplate(models.Model):
    name = models.CharField(max_length=128)
    description = models.TextField(blank=True)

    # What this template applies to. Match priority: role > platform > site > global.
    role = models.ForeignKey(
        "devices.DeviceRole", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="compliance_templates")
    platform = models.CharField(max_length=50, blank=True, help_text="e.g. ios_xe, aos_cx")
    site = models.ForeignKey(
        "devices.Site", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="compliance_templates")

    # The Jinja2 template content (expected config lines).
    template_content = models.TextField(help_text="Jinja2 template defining expected config lines")
    # Default Jinja2 variables, overridable per device via DeviceComplianceOverride.
    variables = models.JSONField(default=dict, blank=True, help_text="Default Jinja2 variables")

    enabled = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class DeviceComplianceOverride(models.Model):
    device = models.ForeignKey(
        "devices.Device", on_delete=models.CASCADE, related_name="compliance_overrides")
    template = models.ForeignKey(
        ComplianceTemplate, on_delete=models.CASCADE, related_name="device_overrides")
    variables = models.JSONField(
        default=dict, help_text="Override template variables for this specific device")

    class Meta:
        # One override row per (device, template) so the engine can .get() it.
        constraints = [
            models.UniqueConstraint(
                fields=["device", "template"], name="unique_device_template_override")
        ]

    def __str__(self):
        return f"{self.device} override for {self.template}"


class ComplianceTemplateResult(models.Model):
    class Status(models.TextChoices):
        COMPLIANT = "compliant", "Compliant"
        NON_COMPLIANT = "non_compliant", "Non-Compliant"
        ERROR = "error", "Error"
        SKIPPED = "skipped", "Skipped"

    device = models.ForeignKey(
        "devices.Device", on_delete=models.CASCADE, related_name="template_compliance_results")
    template = models.ForeignKey(
        ComplianceTemplate, on_delete=models.CASCADE, related_name="results")
    status = models.CharField(max_length=20, choices=Status.choices, db_index=True)
    score = models.FloatField(null=True, help_text="0.0-100.0 compliance %")
    checked_at = models.DateTimeField(auto_now_add=True, db_index=True)
    config_snapshot = models.ForeignKey(
        "configbackup.DeviceConfig", null=True, blank=True, on_delete=models.SET_NULL)

    # Detailed findings — list of {type, severity, line, expected, actual, context}.
    findings = models.JSONField(default=list, help_text="List of compliance findings")
    missing_count = models.IntegerField(default=0)
    extra_count = models.IntegerField(default=0)
    drift_count = models.IntegerField(default=0)
    remediation = models.TextField(blank=True, help_text="Config commands to remediate")

    class Meta:
        ordering = ["-checked_at"]
        indexes = [models.Index(fields=["device", "template", "-checked_at"])]

    def __str__(self):
        return f"{self.device} / {self.template} = {self.status} ({self.score})"


# ── OS version policy & fleet inventory ──────────────────────────────────────

class ApprovedOSVersion(models.Model):
    """An OS-version policy entry: which versions of a platform are approved,
    preferred, deprecated, or prohibited. Drives OS-version compliance scoring.
    """

    class Status(models.TextChoices):
        APPROVED   = "approved",   "Approved"
        PREFERRED  = "preferred",  "Preferred"
        DEPRECATED = "deprecated", "Deprecated - Update Soon"
        PROHIBITED = "prohibited", "Prohibited - Update Now"

    platform = models.CharField(max_length=64, help_text="e.g. ios_xe, aos_cx, fortios")
    version_pattern = models.CharField(
        max_length=128,
        help_text='Exact version or regex pattern. e.g. "17.12.*" or "10.13.1000"',
    )
    is_regex = models.BooleanField(default=False)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.APPROVED)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["platform", "version_pattern"]

    def __str__(self):
        return f"{self.platform} {self.version_pattern} ({self.status})"

    def matches(self, version: str) -> bool:
        """True if `version` matches this policy's pattern."""
        version = version or ""
        if self.is_regex:
            import re
            try:
                return bool(re.match(self.version_pattern, version))
            except re.error:
                return False
        return self.version_pattern == version


class DiscoveredPlatformModel(models.Model):
    """Every unique platform+model+version combo seen across the fleet.

    Auto-populated from device inventory (see
    apps.compliance.os_policy.refresh_discovered_platforms). `os_status` caches
    the computed OS-version compliance status for the combo.
    """

    class Status(models.TextChoices):
        APPROVED   = "approved",   "Approved"
        PREFERRED  = "preferred",  "Preferred"
        DEPRECATED = "deprecated", "Deprecated"
        PROHIBITED = "prohibited", "Prohibited"
        UNKNOWN    = "unknown",    "Not in policy"

    platform = models.CharField(max_length=64)
    model = models.CharField(max_length=128, blank=True)
    os_version = models.CharField(max_length=128, blank=True)
    device_count = models.IntegerField(default=0)
    last_seen = models.DateTimeField(auto_now=True)
    os_status = models.CharField(max_length=20, choices=Status.choices, default=Status.UNKNOWN)

    class Meta:
        unique_together = [["platform", "model", "os_version"]]
        ordering = ["platform", "model"]

    def __str__(self):
        return f"{self.platform}/{self.model}/{self.os_version} (x{self.device_count})"
