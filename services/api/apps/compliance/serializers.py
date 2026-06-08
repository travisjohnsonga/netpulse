from rest_framework import serializers

from .models import (
    ApprovedOSVersion,
    CompliancePolicy,
    CompliancePolicyRule,
    ComplianceResult,
    ComplianceTemplate,
    ComplianceTemplateResult,
    DiscoveredPlatformModel,
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


class ApprovedOSVersionSerializer(serializers.ModelSerializer):
    class Meta:
        model = ApprovedOSVersion
        fields = (
            "id", "platform", "version_pattern", "is_regex", "status",
            "notes", "created_at",
        )
        read_only_fields = ("created_at",)

    def validate(self, attrs):
        # Validate regex patterns up-front so a bad pattern fails the request
        # rather than silently never-matching at scoring time.
        is_regex = attrs.get("is_regex", getattr(self.instance, "is_regex", False))
        pattern = attrs.get("version_pattern", getattr(self.instance, "version_pattern", ""))
        if is_regex:
            import re
            try:
                re.compile(pattern)
            except re.error as exc:
                raise serializers.ValidationError({"version_pattern": f"Invalid regex: {exc}"})
        return attrs


class DiscoveredPlatformModelSerializer(serializers.ModelSerializer):
    class Meta:
        model = DiscoveredPlatformModel
        fields = (
            "id", "platform", "model", "os_version", "device_count",
            "os_status", "last_seen",
        )
