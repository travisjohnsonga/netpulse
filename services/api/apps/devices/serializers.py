from rest_framework import serializers

from .models import Device, DeviceGroup, DiscoveredDevice, DiscoveryJob, Site


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
    display_hostname = serializers.SerializerMethodField()

    class Meta:
        model = Device
        fields = "__all__"
        read_only_fields = ("created_at", "updated_at")

    def get_display_hostname(self, obj):
        return obj.display_hostname

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
    display_hostname = serializers.SerializerMethodField()

    class Meta:
        model = Device
        # Lightweight, but carries enough for the configurable Devices columns
        # (vendor, model, OS, serial, mgmt IP, last seen, credentials, notes).
        fields = (
            "id", "hostname", "display_hostname", "ip_address", "management_ip",
            "platform", "vendor", "model", "os_version", "serial_number", "status",
            "site_name", "credential_profile", "last_seen", "is_reachable",
            "consecutive_failures", "last_reachability_check", "unreachable_since",
            "notes", "created_at",
        )

    def get_display_hostname(self, obj):
        return obj.display_hostname


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


# ── Discovery ─────────────────────────────────────────────────────────────────

def existing_device_for(dd: DiscoveredDevice):
    """
    The inventory Device this discovered device corresponds to, if any:
    its approved_device, else a match on management/IP address or hostname.
    Returns the Device or None.
    """
    from django.db.models import Q

    if dd.approved_device_id:
        return dd.approved_device
    q = Q(management_ip=dd.source_ip) | Q(ip_address=dd.source_ip)
    if dd.discovered_hostname:
        q |= Q(hostname__iexact=dd.discovered_hostname)
    return Device.objects.filter(q).first()


class DiscoveredDeviceSerializer(serializers.ModelSerializer):
    already_exists = serializers.SerializerMethodField()
    existing_device_id = serializers.SerializerMethodField()
    existing_device_hostname = serializers.SerializerMethodField()

    class Meta:
        model = DiscoveredDevice
        fields = "__all__"
        read_only_fields = (
            "job", "source_ip", "detection_methods", "responds_to",
            "confidence_score", "discovered_hostname", "discovered_vendor",
            "discovered_platform", "discovered_model", "discovered_os",
            "raw_fingerprint", "device_category", "os_detected", "os_accuracy",
            "status", "approved_device", "approved_by",
            "approved_at", "created_at", "updated_at",
        )

    def _existing(self, obj):
        # Cache on the instance so the three fields share one query.
        if not hasattr(obj, "_existing_device_cache"):
            obj._existing_device_cache = existing_device_for(obj)
        return obj._existing_device_cache

    def get_already_exists(self, obj) -> bool:
        return self._existing(obj) is not None

    def get_existing_device_id(self, obj):
        dev = self._existing(obj)
        return dev.id if dev else None

    def get_existing_device_hostname(self, obj):
        dev = self._existing(obj)
        return dev.hostname if dev else None


class DiscoveryJobSerializer(serializers.ModelSerializer):
    seed_device_hostname = serializers.CharField(
        source="seed_device.hostname", read_only=True, default=None)
    credential_profile_name = serializers.CharField(
        source="credential_profile.name", read_only=True, default=None)
    pending_count = serializers.SerializerMethodField()
    progress_pct = serializers.SerializerMethodField()

    class Meta:
        model = DiscoveryJob
        fields = "__all__"
        read_only_fields = (
            "status", "devices_found", "started_at", "completed_at",
            "error_message", "created_by", "created_at", "updated_at",
            "progress_current", "progress_total", "progress_message", "ips_scanned",
        )

    def get_progress_pct(self, obj):
        if obj.progress_total > 0:
            return round(min(obj.progress_current / obj.progress_total * 100, 100))
        return 0

    def get_pending_count(self, obj):
        # Avoids N+1 when the viewset annotates; falls back to a count otherwise.
        cached = getattr(obj, "pending_count_annotated", None)
        if cached is not None:
            return cached
        return obj.discovered_devices.filter(status=DiscoveredDevice.Status.PENDING).count()
