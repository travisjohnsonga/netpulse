from rest_framework import serializers

from .models import (
    ApprovedOSVersion,
    CompliancePolicy,
    CompliancePolicyRule,
    ComplianceResult,
    ComplianceTemplate,
    ComplianceTemplateResult,
    DiscoveredPlatformModel,
    InterfaceComplianceResult,
    InterfaceComplianceRule,
    RoleConsistencyRule,
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
                from apps.core.errors import safe_detail
                raise serializers.ValidationError({"version_pattern": safe_detail(
                    exc, context="version_pattern regex",
                    public="Invalid regular expression.")})
        return attrs


class DiscoveredPlatformModelSerializer(serializers.ModelSerializer):
    class Meta:
        model = DiscoveredPlatformModel
        fields = (
            "id", "platform", "model", "os_version", "device_count",
            "os_status", "last_seen",
        )


class InterfaceComplianceRuleSerializer(serializers.ModelSerializer):
    trigger_display = serializers.CharField(source="get_trigger_display", read_only=True)
    result_summary = serializers.SerializerMethodField()

    class Meta:
        model = InterfaceComplianceRule
        fields = (
            "id", "name", "description", "trigger", "trigger_display",
            "trigger_value", "trigger_require_capabilities",
            "trigger_exclude_capabilities", "platform", "checks", "enabled",
            "result_summary", "created_at", "updated_at",
        )
        read_only_fields = ("id", "trigger_display", "result_summary", "created_at", "updated_at")

    def get_result_summary(self, obj) -> dict:
        rows = list(obj.results.all())
        return {
            "total": len(rows),
            "passing": sum(1 for r in rows if r.passed),
            "failing": sum(1 for r in rows if not r.passed),
        }

    def validate_checks(self, value):
        if not isinstance(value, list):
            raise serializers.ValidationError("checks must be a list.")
        for c in value:
            if not isinstance(c, dict) or not c.get("type"):
                raise serializers.ValidationError("each check needs a 'type'.")
        return value

    @staticmethod
    def _validate_capability_list(value):
        if value is None:
            return []
        if not isinstance(value, list):
            raise serializers.ValidationError("Must be a list of capability tokens.")
        return [str(v) for v in value]

    def validate_trigger_require_capabilities(self, value):
        return self._validate_capability_list(value)

    def validate_trigger_exclude_capabilities(self, value):
        return self._validate_capability_list(value)


class InterfaceComplianceResultSerializer(serializers.ModelSerializer):
    device_hostname = serializers.CharField(source="device.hostname", read_only=True)
    rule_name = serializers.CharField(source="rule.name", read_only=True)

    class Meta:
        model = InterfaceComplianceResult
        fields = (
            "id", "rule", "rule_name", "device", "device_hostname", "interface",
            "neighbor", "trigger_match", "passed", "findings", "checks_total",
            "checked_at",
        )
        read_only_fields = fields


class RoleConsistencyRuleSerializer(serializers.ModelSerializer):
    check_type_display = serializers.CharField(source="get_check_type_display", read_only=True)
    role_name = serializers.CharField(source="role.name", read_only=True, default=None)
    site_name = serializers.CharField(source="site.name", read_only=True, default=None)

    class Meta:
        model = RoleConsistencyRule
        fields = (
            "id", "name", "description", "check_type", "check_type_display",
            "role", "role_name", "platform", "site", "site_name",
            "excluded_vlans", "severity", "enabled", "last_run", "last_summary",
            "created_at", "updated_at",
        )
        read_only_fields = ("id", "check_type_display", "role_name", "site_name",
                            "last_run", "last_summary", "created_at", "updated_at")

    def validate_excluded_vlans(self, value):
        if not isinstance(value, list):
            raise serializers.ValidationError("excluded_vlans must be a list of VLAN IDs.")
        return value
