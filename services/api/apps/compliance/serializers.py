from rest_framework import serializers

from .models import (
    CompliancePolicy,
    CompliancePolicyRule,
    ComplianceResult,
    ComplianceTemplate,
    ComplianceTemplateResult,
)


class CompliancePolicyRuleSerializer(serializers.ModelSerializer):
    class Meta:
        model = CompliancePolicyRule
        fields = "__all__"
        read_only_fields = ("created_at", "updated_at")


class CompliancePolicySerializer(serializers.ModelSerializer):
    rules = CompliancePolicyRuleSerializer(many=True, read_only=True)

    class Meta:
        model = CompliancePolicy
        fields = "__all__"
        read_only_fields = ("created_at", "updated_at")


class ComplianceResultSerializer(serializers.ModelSerializer):
    class Meta:
        model = ComplianceResult
        fields = "__all__"
        read_only_fields = ("created_at", "updated_at")


# ── Template-based compliance ───────────────────────────────────────────────────

class ComplianceTemplateSerializer(serializers.ModelSerializer):
    role_name = serializers.CharField(source="role.name", read_only=True, default=None)
    site_name = serializers.CharField(source="site.name", read_only=True, default=None)

    class Meta:
        model = ComplianceTemplate
        fields = (
            "id", "name", "description", "role", "role_name", "platform",
            "site", "site_name", "template_content", "variables", "enabled",
            "created_at", "updated_at", "created_by",
        )
        read_only_fields = ("created_at", "updated_at", "created_by")


class ComplianceTemplateResultSerializer(serializers.ModelSerializer):
    template_name = serializers.CharField(source="template.name", read_only=True, default=None)
    device_hostname = serializers.CharField(source="device.hostname", read_only=True, default=None)

    class Meta:
        model = ComplianceTemplateResult
        fields = (
            "id", "device", "device_hostname", "template", "template_name",
            "status", "score", "checked_at", "config_snapshot", "findings",
            "missing_count", "extra_count", "drift_count", "remediation",
        )
        read_only_fields = fields
