from rest_framework import serializers

from .models import ConfigPush, MonitoredInterface, SNMPGlobalSettings, TelemetryConfig


class TelemetryConfigSerializer(serializers.ModelSerializer):
    effective_intervals = serializers.SerializerMethodField()

    class Meta:
        model = TelemetryConfig
        exclude = ("device",)
        read_only_fields = ("created_at", "updated_at")

    def get_effective_intervals(self, obj) -> dict:
        g = SNMPGlobalSettings.load()
        use = obj.override_intervals
        def eff(dev_val, glob_val):
            return dev_val if (use and dev_val is not None) else glob_val
        return {
            "device_metrics": eff(obj.device_metrics_interval, g.device_metrics_interval),
            "interface_traffic": eff(obj.interface_traffic_interval, g.interface_traffic_interval),
            "interface_status": eff(obj.interface_status_interval, g.interface_status_interval),
            "bgp": eff(obj.bgp_interval, g.bgp_interval),
        }


class PollingSettingsSerializer(serializers.ModelSerializer):
    class Meta:
        model = SNMPGlobalSettings
        exclude = ("id",)
        read_only_fields = ("created_at", "updated_at")


class MonitoredInterfaceSerializer(serializers.ModelSerializer):
    class Meta:
        model = MonitoredInterface
        fields = (
            "id", "if_index", "if_name", "if_description", "if_speed_mbps", "if_type",
            "lldp_neighbor_hostname", "lldp_neighbor_port", "lldp_neighbor_desc",
            "poll_traffic", "poll_errors", "poll_status", "collection_method",
            "last_discovered", "last_status", "last_status_changed",
            "alert_on_down", "alert_on_up", "alert_severity", "consecutive_polls_before_alert",
        )


class DiscoveredInterfaceSerializer(serializers.Serializer):
    if_index = serializers.IntegerField(allow_null=True)
    if_name = serializers.CharField()
    if_description = serializers.CharField(allow_blank=True)
    if_speed_mbps = serializers.IntegerField(allow_null=True)
    if_type = serializers.CharField(allow_blank=True)
    oper_status = serializers.CharField()
    admin_status = serializers.CharField()
    lldp_neighbor_hostname = serializers.CharField(allow_null=True)
    lldp_neighbor_port = serializers.CharField(allow_null=True)
    lldp_neighbor_desc = serializers.CharField(allow_null=True)
    auto_select = serializers.BooleanField()
    collection_method = serializers.CharField()


class _BulkInterfaceItemSerializer(serializers.Serializer):
    if_name = serializers.CharField()
    if_index = serializers.IntegerField(required=False, allow_null=True)
    if_description = serializers.CharField(required=False, allow_blank=True, default="")
    if_speed_mbps = serializers.IntegerField(required=False, allow_null=True)
    if_type = serializers.CharField(required=False, allow_blank=True, default="")
    lldp_neighbor_hostname = serializers.CharField(required=False, allow_null=True)
    lldp_neighbor_port = serializers.CharField(required=False, allow_null=True)
    lldp_neighbor_desc = serializers.CharField(required=False, allow_null=True)
    poll_traffic = serializers.BooleanField(required=False, default=True)
    poll_errors = serializers.BooleanField(required=False, default=True)
    poll_status = serializers.BooleanField(required=False, default=True)
    collection_method = serializers.ChoiceField(
        choices=["auto", "snmp", "gnmi"], required=False, default="auto")
    oper_status = serializers.CharField(required=False, allow_blank=True)
    alert_on_down = serializers.BooleanField(required=False, default=True)
    alert_on_up = serializers.BooleanField(required=False, default=True)
    alert_severity = serializers.ChoiceField(
        choices=MonitoredInterface.AlertSeverity.choices, required=False, default="high")
    consecutive_polls_before_alert = serializers.IntegerField(required=False, default=1, min_value=1)


class InterfaceBulkSaveSerializer(serializers.Serializer):
    interfaces = _BulkInterfaceItemSerializer(many=True)


class InterfaceAlertConfigSerializer(serializers.Serializer):
    """Bulk-apply alert settings to a set of the device's interfaces (by name)."""
    if_names = serializers.ListField(child=serializers.CharField())
    alert_on_down = serializers.BooleanField(required=False)
    alert_on_up = serializers.BooleanField(required=False)
    alert_severity = serializers.ChoiceField(
        choices=["critical", "high", "medium", "low"], required=False)
    consecutive_polls_before_alert = serializers.IntegerField(required=False, min_value=1)


class _ConfigSectionSerializer(serializers.Serializer):
    enabled = serializers.BooleanField()
    config = serializers.CharField(allow_null=True)


class GeneratedConfigSerializer(serializers.Serializer):
    platform = serializers.CharField(allow_blank=True)
    vendor = serializers.CharField(allow_blank=True)
    collector_ip = serializers.CharField(allow_blank=True)
    sections = serializers.DictField(child=_ConfigSectionSerializer())
    full_config = serializers.CharField(allow_blank=True)


class PushRequestSerializer(serializers.Serializer):
    sections = serializers.ListField(child=serializers.CharField())


class PushResponseSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    pushed_sections = serializers.ListField(child=serializers.CharField())
    output = serializers.CharField(allow_blank=True)
    errors = serializers.ListField(child=serializers.CharField())


class ConfigPushSerializer(serializers.ModelSerializer):
    pushed_by_username = serializers.CharField(source="pushed_by.username", read_only=True, default=None)

    class Meta:
        model = ConfigPush
        fields = ("id", "sections", "success", "output", "errors",
                  "pushed_by", "pushed_by_username", "created_at")
