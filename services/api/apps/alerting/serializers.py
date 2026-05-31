from rest_framework import serializers

from .models import (
    AlertAcknowledgement, AlertNotification, AlertRoute, ContactMethod,
    EscalationPolicy, EscalationStep, MaintenanceWindow, OnCallSchedule,
    OnCallShift, Team, TeamMember,
)


class TeamMemberSerializer(serializers.ModelSerializer):
    username = serializers.CharField(source="user.username", read_only=True)

    class Meta:
        model = TeamMember
        fields = ("id", "team", "user", "username", "role",
                  "notify_email", "notify_sms", "notify_slack")
        read_only_fields = ("team",)


class TeamSerializer(serializers.ModelSerializer):
    member_count = serializers.IntegerField(source="memberships.count", read_only=True)

    class Meta:
        model = Team
        fields = ("id", "name", "description", "color", "slack_webhook_url",
                  "discord_webhook_url", "member_count", "created_at", "updated_at")
        read_only_fields = ("created_at", "updated_at")


class ContactMethodSerializer(serializers.ModelSerializer):
    class Meta:
        model = ContactMethod
        fields = "__all__"
        read_only_fields = ("verified", "created_at", "updated_at")


class EscalationStepSerializer(serializers.ModelSerializer):
    class Meta:
        model = EscalationStep
        fields = ("id", "policy", "step_number", "delay_minutes",
                  "notify_team", "notify_user", "notify_type")
        read_only_fields = ("policy",)


class EscalationPolicySerializer(serializers.ModelSerializer):
    steps = EscalationStepSerializer(many=True, read_only=True)

    class Meta:
        model = EscalationPolicy
        fields = ("id", "name", "description", "team", "repeat_interval_minutes",
                  "steps", "created_at", "updated_at")
        read_only_fields = ("created_at", "updated_at")


class AlertRouteSerializer(serializers.ModelSerializer):
    policy_name = serializers.CharField(source="escalation_policy.name", read_only=True)

    class Meta:
        model = AlertRoute
        fields = "__all__"
        read_only_fields = ("created_at", "updated_at")


class OnCallShiftSerializer(serializers.ModelSerializer):
    username = serializers.CharField(source="user.username", read_only=True)

    class Meta:
        model = OnCallShift
        fields = ("id", "schedule", "user", "username", "start_datetime",
                  "end_datetime", "recurrence", "recurrence_days")
        read_only_fields = ("schedule",)


class OnCallScheduleSerializer(serializers.ModelSerializer):
    shifts = OnCallShiftSerializer(many=True, read_only=True)

    class Meta:
        model = OnCallSchedule
        fields = ("id", "team", "name", "timezone", "shifts", "created_at", "updated_at")
        read_only_fields = ("created_at", "updated_at")


class AlertAcknowledgementSerializer(serializers.ModelSerializer):
    acknowledged_by_username = serializers.CharField(source="acknowledged_by.username", read_only=True)

    class Meta:
        model = AlertAcknowledgement
        fields = ("id", "alert_event", "acknowledged_by", "acknowledged_by_username",
                  "acknowledged_at", "note", "snoozed_until")
        read_only_fields = fields


class AlertNotificationSerializer(serializers.ModelSerializer):
    username = serializers.CharField(source="user.username", read_only=True, default=None)

    class Meta:
        model = AlertNotification
        fields = ("id", "alert_event", "escalation_step", "user", "username",
                  "team", "channel", "status", "sent_at", "error", "created_at")
        read_only_fields = fields


class MaintenanceWindowSerializer(serializers.ModelSerializer):
    is_currently_active = serializers.BooleanField(read_only=True)
    device_names = serializers.SerializerMethodField()
    site_names = serializers.SerializerMethodField()

    class Meta:
        model = MaintenanceWindow
        fields = "__all__"
        read_only_fields = ("created_by", "created_at", "updated_at")

    def get_device_names(self, obj):
        return list(obj.devices.values_list("hostname", flat=True))

    def get_site_names(self, obj):
        return list(obj.sites.values_list("name", flat=True))
