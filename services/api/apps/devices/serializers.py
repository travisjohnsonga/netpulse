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
    # Resolved collector display info (assigned → site default → global default).
    collector_name = serializers.SerializerMethodField()
    collector_ip = serializers.SerializerMethodField()
    collector_status = serializers.SerializerMethodField()

    class Meta:
        model = Device
        fields = "__all__"
        read_only_fields = ("created_at", "updated_at")

    def _effective(self, obj):
        from apps.collectors.resolve import effective_collector
        return effective_collector(obj)

    def get_collector_name(self, obj):
        c = self._effective(obj)
        return c.name if c else None

    def get_collector_ip(self, obj):
        from apps.collectors.resolve import effective_collector_ip
        return effective_collector_ip(obj) or None

    def get_collector_status(self, obj):
        c = self._effective(obj)
        return c.status if c else None


class DeviceListSerializer(serializers.ModelSerializer):
    site_name = serializers.CharField(source="site.name", read_only=True, default=None)

    class Meta:
        model = Device
        # Lightweight, but carries enough for the configurable Devices columns
        # (vendor, model, OS, serial, mgmt IP, last seen, credentials, notes).
        fields = (
            "id", "hostname", "ip_address", "management_ip", "platform", "vendor",
            "model", "os_version", "serial_number", "status", "site_name",
            "credential_profile", "last_seen", "notes", "created_at",
        )


class TestConnectionRequestSerializer(serializers.Serializer):
    ip = serializers.IPAddressField(help_text="IP address to probe.")
    credential_profile_id = serializers.IntegerField(
        required=False, help_text="If given, also run SSHDetect to identify the platform.")


class DetectPlatformRequestSerializer(serializers.Serializer):
    ip = serializers.IPAddressField()
    credential_profile_id = serializers.IntegerField()


class DetectPlatformResponseSerializer(serializers.Serializer):
    detected = serializers.BooleanField()
    device_type = serializers.CharField(required=False, allow_null=True)
    vendor = serializers.CharField(required=False, allow_null=True)
    platform = serializers.CharField(required=False, allow_null=True)
    os_version = serializers.CharField(required=False, allow_null=True)
    hostname = serializers.CharField(required=False, allow_null=True)
    model = serializers.CharField(required=False, allow_null=True)
    serial = serializers.CharField(required=False, allow_null=True)
    confidence = serializers.CharField(required=False, allow_null=True)
    all_matches = serializers.ListField(child=serializers.CharField(), required=False)
    error = serializers.CharField(required=False, allow_null=True)
    best_guess = serializers.CharField(required=False, allow_null=True)


class TestConnectionResponseSerializer(serializers.Serializer):
    reachable = serializers.BooleanField()
    open_ports = serializers.ListField(child=serializers.IntegerField())
    banner = serializers.CharField(allow_blank=True)
    vendor = serializers.CharField(allow_null=True)
    platform = serializers.CharField(allow_null=True)
    os_version = serializers.CharField(allow_null=True)
    model = serializers.CharField(allow_null=True)
    detail = serializers.CharField()
