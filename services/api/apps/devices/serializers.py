from rest_framework import serializers

from .models import Device, DeviceGroup, Site


class SiteSerializer(serializers.ModelSerializer):
    device_count = serializers.IntegerField(source="devices.count", read_only=True)
    parent_site_name = serializers.CharField(source="parent_site.name", read_only=True, default=None)

    class Meta:
        model = Site
        fields = "__all__"
        read_only_fields = ("slug", "created_at", "updated_at")


class DeviceGroupSerializer(serializers.ModelSerializer):
    class Meta:
        model = DeviceGroup
        fields = "__all__"
        read_only_fields = ("created_at", "updated_at")


class DeviceSerializer(serializers.ModelSerializer):
    class Meta:
        model = Device
        fields = "__all__"
        read_only_fields = ("created_at", "updated_at")


class DeviceListSerializer(serializers.ModelSerializer):
    site_name = serializers.CharField(source="site.name", read_only=True, default=None)

    class Meta:
        model = Device
        fields = ("id", "hostname", "ip_address", "platform", "status", "site_name", "created_at")


class TestConnectionRequestSerializer(serializers.Serializer):
    ip = serializers.IPAddressField(help_text="IP address to probe.")


class TestConnectionResponseSerializer(serializers.Serializer):
    reachable = serializers.BooleanField()
    open_ports = serializers.ListField(child=serializers.IntegerField())
    banner = serializers.CharField(allow_blank=True)
    vendor = serializers.CharField(allow_null=True)
    platform = serializers.CharField(allow_null=True)
    os_version = serializers.CharField(allow_null=True)
    model = serializers.CharField(allow_null=True)
    detail = serializers.CharField()
