from rest_framework import serializers

from .models import CheckResult, ServiceCheck, ServiceCheckCollector


class ServiceCheckCollectorSerializer(serializers.ModelSerializer):
    collector_name = serializers.CharField(source="collector.name", read_only=True)
    collector_ip = serializers.CharField(source="collector.collector_ip", read_only=True, default=None)
    collector_status = serializers.CharField(source="collector.status", read_only=True)

    class Meta:
        model = ServiceCheckCollector
        fields = (
            "id", "collector", "collector_name", "collector_ip", "collector_status",
            "enabled", "last_result", "last_checked", "last_latency_ms",
            "last_error", "consecutive_failures",
        )
        read_only_fields = (
            "last_result", "last_checked", "last_latency_ms", "last_error",
            "consecutive_failures",
        )


class ServiceCheckSerializer(serializers.ModelSerializer):
    device_hostname = serializers.CharField(source="device.hostname", read_only=True, default=None)
    site_name = serializers.CharField(source="site.name", read_only=True, default=None)
    effective_port = serializers.IntegerField(read_only=True)
    last_response_ms = serializers.SerializerMethodField()
    last_details = serializers.SerializerMethodField()
    # Per-collector vantage-point breakdown (read-only; managed via the
    # collectors/ sub-resource). `collectors` itself is read-only because the
    # M2M uses a custom through model.
    collector_results = ServiceCheckCollectorSerializer(
        source="collector_assignments", many=True, read_only=True)

    class Meta:
        model = ServiceCheck
        fields = "__all__"
        read_only_fields = (
            "current_status", "last_checked", "last_status_change",
            "consecutive_failures", "collectors", "created_at", "updated_at",
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
    collector_name = serializers.CharField(source="collector.name", read_only=True, default=None)

    class Meta:
        model = CheckResult
        fields = ("id", "check", "collector", "collector_name", "status",
                  "response_time_ms", "checked_at", "error", "details")
        read_only_fields = fields
