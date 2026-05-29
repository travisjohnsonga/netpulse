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
