from rest_framework import serializers

from .models import ConfigPush, MonitoredInterface, TelemetryConfig


class TelemetryConfigSerializer(serializers.ModelSerializer):
    class Meta:
        model = TelemetryConfig
        exclude = ("device",)
        read_only_fields = ("created_at", "updated_at")


class MonitoredInterfaceSerializer(serializers.ModelSerializer):
    class Meta:
        model = MonitoredInterface
        fields = (
            "id", "if_index", "if_name", "if_description", "if_speed_mbps", "if_type",
            "lldp_neighbor_hostname", "lldp_neighbor_port", "lldp_neighbor_desc",
            "poll_traffic", "poll_errors", "poll_status", "collection_method",
            "last_discovered", "last_status",
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


class InterfaceBulkSaveSerializer(serializers.Serializer):
    interfaces = _BulkInterfaceItemSerializer(many=True)


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
