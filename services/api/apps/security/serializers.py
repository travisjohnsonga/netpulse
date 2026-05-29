from rest_framework import serializers

from .models import DeviceRiskScore


class DeviceRiskScoreSerializer(serializers.ModelSerializer):
    hostname = serializers.CharField(source="device.hostname", read_only=True)

    class Meta:
        model = DeviceRiskScore
        fields = "__all__"
        read_only_fields = ("last_computed_at", "created_at", "updated_at")
