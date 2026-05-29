from rest_framework import serializers

from .models import CompliancePolicy, CompliancePolicyRule, ComplianceResult


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
