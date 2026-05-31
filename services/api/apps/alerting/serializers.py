from rest_framework import serializers

from .models import (
    AlertNotification, AlertRoute, ContactMethod, EscalationPolicy,
    EscalationStep, Team, TeamMember,
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
        fields = ("id", "name", "description", "color", "member_count", "created_at", "updated_at")
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


class AlertNotificationSerializer(serializers.ModelSerializer):
    username = serializers.CharField(source="user.username", read_only=True, default=None)

    class Meta:
        model = AlertNotification
        fields = ("id", "alert_event", "escalation_step", "user", "username",
                  "team", "channel", "status", "sent_at", "error", "created_at")
        read_only_fields = fields
