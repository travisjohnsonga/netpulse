from rest_framework import serializers

from .models import LifecycleMilestone


class LifecycleMilestoneSerializer(serializers.ModelSerializer):
    hostname = serializers.CharField(source="device.hostname", read_only=True)

    class Meta:
        model = LifecycleMilestone
        fields = "__all__"
        read_only_fields = ("created_at", "updated_at")
