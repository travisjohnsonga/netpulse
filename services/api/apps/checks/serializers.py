from rest_framework import serializers

from .models import CheckResult, ServiceCheck


class ServiceCheckSerializer(serializers.ModelSerializer):
    device_hostname = serializers.CharField(source="device.hostname", read_only=True, default=None)
    site_name = serializers.CharField(source="site.name", read_only=True, default=None)
    effective_port = serializers.IntegerField(read_only=True)
    last_response_ms = serializers.SerializerMethodField()
    last_details = serializers.SerializerMethodField()

    class Meta:
        model = ServiceCheck
        fields = "__all__"
        read_only_fields = (
            "current_status", "last_checked", "last_status_change",
            "consecutive_failures", "created_at", "updated_at",
        )

    def _latest(self, obj):
        # Fetch the most recent result once per object and cache it.
        if not hasattr(obj, "_latest_result"):
            obj._latest_result = obj.results.order_by("-checked_at").first()
        return obj._latest_result

    def get_last_response_ms(self, obj):
        latest = self._latest(obj)
        return latest.response_time_ms if latest else None

    def get_last_details(self, obj):
        # Per-probe measurements (e.g. TLS days_remaining, ICMP packet_loss_pct).
        latest = self._latest(obj)
        return latest.details if latest else {}


class CheckResultSerializer(serializers.ModelSerializer):
    # Expose the FK as "check" even though the model attribute is service_check.
    check = serializers.PrimaryKeyRelatedField(source="service_check", read_only=True)

    class Meta:
        model = CheckResult
        fields = ("id", "check", "status", "response_time_ms", "checked_at", "error", "details")
        read_only_fields = fields
