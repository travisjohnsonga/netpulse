from rest_framework import serializers

from .models import AlertChannel, AlertEvent, AlertRule


class AlertChannelSerializer(serializers.ModelSerializer):
    class Meta:
        model = AlertChannel
        fields = "__all__"
        read_only_fields = ("created_at", "updated_at")


class AlertRuleSerializer(serializers.ModelSerializer):
    class Meta:
        model = AlertRule
        fields = "__all__"
        read_only_fields = ("created_at", "updated_at")


class AlertEventSerializer(serializers.ModelSerializer):
    rule_name = serializers.CharField(source="rule.name", read_only=True)
    severity = serializers.CharField(source="rule.severity", read_only=True)

    class Meta:
        model = AlertEvent
        fields = "__all__"
        read_only_fields = ("created_at", "updated_at")
