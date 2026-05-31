from rest_framework import serializers

from .models import CheckResult, ServiceCheck


class ServiceCheckSerializer(serializers.ModelSerializer):
    device_hostname = serializers.CharField(source="device.hostname", read_only=True, default=None)
    site_name = serializers.CharField(source="site.name", read_only=True, default=None)
    effective_port = serializers.IntegerField(read_only=True)
    last_response_ms = serializers.SerializerMethodField()

    class Meta:
        model = ServiceCheck
        fields = "__all__"
        read_only_fields = (
            "current_status", "last_checked", "last_status_change",
            "consecutive_failures", "created_at", "updated_at",
        )

    def get_last_response_ms(self, obj):
        latest = obj.results.order_by("-checked_at").values_list("response_time_ms", flat=True).first()
        return latest


class CheckResultSerializer(serializers.ModelSerializer):
    # Expose the FK as "check" even though the model attribute is service_check.
    check = serializers.PrimaryKeyRelatedField(source="service_check", read_only=True)

    class Meta:
        model = CheckResult
        fields = ("id", "check", "status", "response_time_ms", "checked_at", "error", "details")
        read_only_fields = fields
