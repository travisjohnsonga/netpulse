from django.db import models
from rest_framework import serializers

from .models import (
    Device, DeviceGroup, DeviceRole, DiscoveredDevice, DiscoveryJob,
    HostnameRule, LLDPNeighbor, ManualTopologyLink, Site,
)


class DeviceRoleSerializer(serializers.ModelSerializer):
    device_count = serializers.IntegerField(source="devices.count", read_only=True)

    class Meta:
        model = DeviceRole
        fields = (
            "id", "name", "slug", "color", "description", "icon",
            "device_count", "created_at", "updated_at",
        )
        read_only_fields = ("slug", "device_count", "created_at", "updated_at")


class HostnameRuleSerializer(serializers.ModelSerializer):
    role_name = serializers.CharField(source="role.name", read_only=True, default=None)
    role_color = serializers.CharField(source="role.color", read_only=True, default=None)
    site_name = serializers.CharField(source="site.name", read_only=True, default=None)

    class Meta:
        model = HostnameRule
        fields = (
            "id", "name", "pattern", "rule_type", "role", "role_name",
            "role_color", "site", "site_name", "priority", "enabled",
            "created_at", "updated_at",
        )
        read_only_fields = ("created_at", "updated_at")

    def validate_pattern(self, value):
        import re
        try:
            re.compile(value)
        except re.error:
            raise serializers.ValidationError("Invalid regular expression.")
        return value


class HostnameRuleTestSerializer(serializers.Serializer):
    pattern = serializers.CharField()
    hostnames = serializers.ListField(child=serializers.CharField(), allow_empty=False)

    def validate_pattern(self, value):
        import re
        try:
            re.compile(value)
        except re.error:
            raise serializers.ValidationError("Invalid regular expression.")
        return value


class SiteSerializer(serializers.ModelSerializer):
    # device_count + the up/down/unknown breakdown. The SiteViewSet annotates
    # these on the list/detail queryset for efficiency; the fallbacks keep them
    # correct for instances that aren't annotated (e.g. the create response).
    device_count = serializers.SerializerMethodField()
    devices_up = serializers.SerializerMethodField()
    devices_down = serializers.SerializerMethodField()
    devices_unknown = serializers.SerializerMethodField()
    # Server (agent) + service-check up/down counts, mirroring the device counts.
    server_count = serializers.SerializerMethodField()
    servers_up = serializers.SerializerMethodField()
    servers_down = serializers.SerializerMethodField()
    check_count = serializers.SerializerMethodField()
    checks_up = serializers.SerializerMethodField()
    checks_down = serializers.SerializerMethodField()
    parent_site_name = serializers.CharField(source="parent_site.name", read_only=True, default=None)

    class Meta:
        model = Site
        fields = "__all__"
        read_only_fields = ("slug", "created_at", "updated_at")

    def get_device_count(self, obj):
        val = getattr(obj, "device_count", None)
        return val if val is not None else obj.devices.count()

    def get_devices_up(self, obj):
        val = getattr(obj, "devices_up", None)
        return val if val is not None else obj.devices.filter(
            is_reachable=True, status=Device.Status.ACTIVE).count()

    def get_devices_down(self, obj):
        val = getattr(obj, "devices_down", None)
        return val if val is not None else obj.devices.filter(
            models.Q(is_reachable=False)
            | models.Q(status__in=[Device.Status.INACTIVE, Device.Status.UNREACHABLE])).count()

    def get_devices_unknown(self, obj):
        val = getattr(obj, "devices_unknown", None)
        return val if val is not None else obj.devices.filter(is_reachable__isnull=True).count()

    # --- Servers (agents link to a site via their Device: Agent.device → site) ---
    def get_server_count(self, obj):
        val = getattr(obj, "server_count", None)
        if val is not None:
            return val
        from apps.agents.models import Agent
        return Device.objects.filter(
            site=obj, agent__status__in=[Agent.Status.ACTIVE, Agent.Status.INACTIVE]).count()

    def get_servers_up(self, obj):
        val = getattr(obj, "servers_up", None)
        if val is not None:
            return val
        from datetime import timedelta
        from django.utils import timezone
        from apps.agents.models import AGENT_ONLINE_SECONDS, Agent
        cutoff = timezone.now() - timedelta(seconds=AGENT_ONLINE_SECONDS)
        return Device.objects.filter(
            site=obj, agent__status=Agent.Status.ACTIVE, agent__last_seen__gte=cutoff).count()

    def get_servers_down(self, obj):
        val = getattr(obj, "servers_down", None)
        if val is not None:
            return val
        from datetime import timedelta
        from django.utils import timezone
        from apps.agents.models import AGENT_ONLINE_SECONDS, Agent
        cutoff = timezone.now() - timedelta(seconds=AGENT_ONLINE_SECONDS)
        return Device.objects.filter(
            site=obj, agent__status__in=[Agent.Status.ACTIVE, Agent.Status.INACTIVE]
        ).filter(
            models.Q(agent__status=Agent.Status.INACTIVE)
            | models.Q(agent__last_seen__isnull=True)
            | models.Q(agent__last_seen__lt=cutoff)
        ).count()

    # --- Service checks (linked to the site directly via ServiceCheck.site) ---
    def get_check_count(self, obj):
        val = getattr(obj, "check_count", None)
        return val if val is not None else obj.service_checks.filter(is_active=True).count()

    def get_checks_up(self, obj):
        val = getattr(obj, "checks_up", None)
        if val is not None:
            return val
        from apps.checks.models import ServiceCheck
        return obj.service_checks.filter(
            is_active=True, current_status=ServiceCheck.Status.UP).count()

    def get_checks_down(self, obj):
        val = getattr(obj, "checks_down", None)
        if val is not None:
            return val
        from apps.checks.models import ServiceCheck
        return obj.service_checks.filter(
            is_active=True,
            current_status__in=[ServiceCheck.Status.DOWN, ServiceCheck.Status.DEGRADED]).count()


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
    # Role: nested object on read, `role_id` on write (matches the UI dropdown).
    role = DeviceRoleSerializer(read_only=True)
    role_id = serializers.PrimaryKeyRelatedField(
        queryset=DeviceRole.objects.all(), source="role",
        write_only=True, required=False, allow_null=True,
    )

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


def _score_grade(score):
    if score is None:
        return None
    if score >= 90:
        return "A"
    if score >= 80:
        return "B"
    if score >= 70:
        return "C"
    if score >= 60:
        return "D"
    return "F"


class DeviceListSerializer(serializers.ModelSerializer):
    site_name = serializers.CharField(source="site.name", read_only=True, default=None)
    display_hostname = serializers.SerializerMethodField()
    role = DeviceRoleSerializer(read_only=True)
    # Latest stored template-compliance score (annotated on the list queryset —
    # see DeviceViewSet.get_queryset; cheap, no live calls) + derived letter grade.
    compliance_score = serializers.SerializerMethodField()
    compliance_grade = serializers.SerializerMethodField()

    class Meta:
        model = Device
        # Lightweight, but carries enough for the configurable Devices columns
        # (vendor, model, OS, serial, mgmt IP, last seen, credentials, notes, role).
        fields = (
            "id", "hostname", "display_hostname", "ip_address", "management_ip",
            "ip_locked",
            "platform", "vendor", "model", "os_version", "serial_number", "status",
            "site_name", "role", "credential_profile", "last_seen", "is_reachable",
            "consecutive_failures", "last_reachability_check", "unreachable_since",
            "compliance_score", "compliance_grade",
            "notes", "created_at",
        )

    def get_display_hostname(self, obj):
        return obj.display_hostname

    def get_compliance_score(self, obj):
        score = getattr(obj, "compliance_score", None)
        return round(score) if score is not None else None

    def get_compliance_grade(self, obj):
        return _score_grade(getattr(obj, "compliance_score", None))


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
    site_name = serializers.CharField(source="site.name", read_only=True, default=None)
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

    @staticmethod
    def _validate_cidr_list(value, field):
        """Each entry must parse as an IP network/address. Rejects anything else
        — critically, values like "--script=..." or "-oN" that would otherwise be
        passed straight to nmap as argv flags (run_discovery builds the nmap
        command from these lists), i.e. authenticated nmap-option injection."""
        import ipaddress
        if value in (None, ""):
            return value
        if not isinstance(value, list):
            raise serializers.ValidationError(f"{field} must be a list of CIDRs/IPs.")
        for entry in value:
            s = str(entry).strip()
            try:
                ipaddress.ip_network(s, strict=False)
            except ValueError:
                raise serializers.ValidationError(
                    f"{field}: '{entry}' is not a valid IP address or CIDR.")
        return value

    def validate_subnets(self, value):
        return self._validate_cidr_list(value, "subnets")

    def validate_excluded_subnets(self, value):
        return self._validate_cidr_list(value, "excluded_subnets")

    def validate_allowed_subnets(self, value):
        return self._validate_cidr_list(value, "allowed_subnets")

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


class LLDPNeighborSerializer(serializers.ModelSerializer):
    """An LLDP neighbor row for the "Not in Inventory" page.

    `in_inventory` and `guessed_platform` are computed in the view (passed via
    serializer context) so the live re-check and platform guess happen once per
    request, not per row.
    """

    seen_by_device_id = serializers.IntegerField(source="seen_by_id", read_only=True)
    seen_by_device_hostname = serializers.CharField(source="seen_by.hostname", read_only=True)
    seen_on_interface = serializers.CharField(source="local_interface", read_only=True)
    in_inventory = serializers.SerializerMethodField()
    guessed_platform = serializers.SerializerMethodField()

    class Meta:
        model = LLDPNeighbor
        fields = [
            "id", "chassis_id", "chassis_id_type", "port_id", "port_description",
            "system_name", "system_description", "management_address",
            "capabilities", "seen_by_device_id", "seen_by_device_hostname",
            "seen_on_interface", "first_seen", "last_seen", "in_inventory",
            "guessed_platform",
        ]

    def get_in_inventory(self, obj) -> bool:
        idx = self.context.get("inventory_index")
        if idx is None:
            return bool(obj.matched_device_id)
        from .lldp import neighbor_in_inventory
        return neighbor_in_inventory(obj, idx[0], idx[1])

    def get_guessed_platform(self, obj) -> str:
        from .lldp import guess_platform
        return guess_platform(obj.system_description)


class ManualTopologyLinkSerializer(serializers.ModelSerializer):
    """Operator-defined topology link (for devices without LLDP/CDP)."""

    device_a_hostname = serializers.CharField(source="device_a.hostname", read_only=True)
    device_b_hostname = serializers.CharField(source="device_b.hostname", read_only=True)
    link_type_display = serializers.CharField(source="get_link_type_display", read_only=True)
    created_by_username = serializers.CharField(source="created_by.username", read_only=True)
    site_a = serializers.IntegerField(source="device_a.site_id", read_only=True)
    site_b = serializers.IntegerField(source="device_b.site_id", read_only=True)
    # Defaults so the auto UniqueTogetherValidator doesn't force these as required.
    interface_a = serializers.CharField(required=False, allow_blank=True, default="", max_length=64)
    interface_b = serializers.CharField(required=False, allow_blank=True, default="", max_length=64)

    class Meta:
        model = ManualTopologyLink
        fields = (
            "id", "device_a", "device_a_hostname", "interface_a",
            "device_b", "device_b_hostname", "interface_b",
            "link_type", "link_type_display", "speed_mbps", "description",
            "created_by_username", "site_a", "site_b", "created_at", "updated_at",
        )
        read_only_fields = ("created_by_username", "created_at", "updated_at")

    def validate(self, attrs):
        a = attrs.get("device_a", getattr(self.instance, "device_a", None))
        b = attrs.get("device_b", getattr(self.instance, "device_b", None))
        if a and b and a == b:
            raise serializers.ValidationError("A link must connect two different devices.")
        return attrs
