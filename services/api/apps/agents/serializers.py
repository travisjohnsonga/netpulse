from rest_framework import serializers

from .models import (
    DEFAULT_AGENT_CONFIG, LOG_PATH_ALLOWLIST_ROOT, Agent, AgentEnrollmentToken,
    AgentRole, AgentRoleStatus, ServerRole, is_allowed_log_path,
)


class _DiskConfigSerializer(serializers.Serializer):
    exclude_mounts = serializers.ListField(
        child=serializers.CharField(max_length=255), required=False)
    include_mounts = serializers.ListField(
        child=serializers.CharField(max_length=255), required=False)


class _LogsConfigSerializer(serializers.Serializer):
    security_profile = serializers.BooleanField(required=False)
    additional_paths = serializers.ListField(
        child=serializers.CharField(max_length=512), required=False)

    def validate_additional_paths(self, value):
        # Allowlist guardrail: the agent runs as root, so an operator-added path
        # must stay under /var/log/ and never name a secret — reject otherwise
        # (mirrored agent-side as defense in depth).
        bad = [p for p in value if not is_allowed_log_path(p)]
        if bad:
            raise serializers.ValidationError(
                f"Log paths must be under {LOG_PATH_ALLOWLIST_ROOT} and contain no "
                f"secret/traversal names. Rejected: {bad}")
        return value


class AgentConfigSerializer(serializers.Serializer):
    """Validates a (partial) desired-config PATCH. Unknown collection keys are
    rejected so a typo can't silently disable nothing; log paths are allowlisted."""
    collection = serializers.DictField(child=serializers.BooleanField(), required=False)
    interval_seconds = serializers.IntegerField(min_value=10, max_value=3600, required=False)
    disk = _DiskConfigSerializer(required=False)
    logs = _LogsConfigSerializer(required=False)

    def validate_collection(self, value):
        allowed = set(DEFAULT_AGENT_CONFIG["collection"])
        unknown = set(value) - allowed
        if unknown:
            raise serializers.ValidationError(
                f"Unknown collection keys: {sorted(unknown)}. Allowed: {sorted(allowed)}.")
        return value


class AgentLivenessSerializer(serializers.Serializer):
    """Writable per-agent liveness-alert config (PATCH /api/servers/{id}/liveness/).
    offline_threshold_seconds=null → use the global default; liveness_alerts_enabled
    =False suppresses the offline alert for a host that legitimately sleeps."""
    offline_threshold_seconds = serializers.IntegerField(
        min_value=30, max_value=86400, required=False, allow_null=True)
    liveness_alerts_enabled = serializers.BooleanField(required=False)


class ServerRoleSerializer(serializers.ModelSerializer):
    agent_count = serializers.IntegerField(source="agents.count", read_only=True)

    class Meta:
        model = ServerRole
        fields = (
            "id", "name", "role_type", "description", "windows_services",
            "linux_services", "port_checks", "custom_checks", "is_builtin",
            "agent_count", "created_at",
        )
        read_only_fields = ("id", "is_builtin", "agent_count", "created_at")


class AgentSerializer(serializers.ModelSerializer):
    device_id = serializers.IntegerField(source="device.id", read_only=True, default=None)
    site_name = serializers.CharField(source="device.site.name", read_only=True, default=None)
    role_types = serializers.SerializerMethodField()
    # Authoritative online state (same threshold the liveness alert uses) so the
    # UI badge agrees with alerting instead of computing its own window.
    is_online = serializers.BooleanField(read_only=True)

    class Meta:
        model = Agent
        fields = (
            "id", "hostname", "device_id", "site_name", "os", "os_name",
            "os_version", "os_kernel", "arch", "version",
            "cert_serial", "cert_expires_at", "status", "collection_interval",
            "role_types", "last_seen", "is_online",
            "offline_threshold_seconds", "liveness_alerts_enabled", "created_at",
        )
        read_only_fields = fields

    def get_role_types(self, obj) -> list[str]:
        return list(obj.server_roles.values_list("role_type", flat=True))


class ServerSerializer(serializers.ModelSerializer):
    """An agent-monitored server for the Servers list, with latest metrics."""
    agent_version = serializers.CharField(source="version", read_only=True)
    device_id = serializers.IntegerField(source="device.id", read_only=True, default=None)
    os_version = serializers.SerializerMethodField()
    site = serializers.SerializerMethodField()
    roles = serializers.SerializerMethodField()
    latest_metrics = serializers.SerializerMethodField()
    is_online = serializers.BooleanField(read_only=True)
    # General running-services list (Agent.reported_services), populated only when
    # the 'services' collection toggle is on. services_collected reflects that
    # toggle so the UI can distinguish "toggle off" from "on, no data yet".
    services_collected = serializers.SerializerMethodField()

    class Meta:
        model = Agent
        fields = (
            "id", "hostname", "os", "os_name", "os_version", "os_kernel", "arch",
            "status", "last_seen", "is_online",
            "offline_threshold_seconds", "liveness_alerts_enabled",
            "agent_version", "cert_expires_at", "collection_interval",
            "device_id", "site", "roles", "latest_metrics",
            "reported_services", "services_collected", "created_at",
        )
        read_only_fields = fields

    def get_services_collected(self, obj) -> bool:
        return bool(obj.effective_config().get("collection", {}).get("services", False))

    def get_os_version(self, obj) -> str:
        # Prefer the AGENT's own reported OS version (OS-detail); fall back to the
        # linked Device's firmware field (blank for agent-created devices), then "".
        return (obj.os_version
                or getattr(getattr(obj, "device", None), "os_version", "")
                or "")

    def get_site(self, obj):
        site = getattr(getattr(obj, "device", None), "site", None)
        return {"id": site.id, "name": site.name} if site else None

    def get_roles(self, obj) -> list[str]:
        return list(obj.assigned_roles.values_list("role__role_type", flat=True))

    def get_latest_metrics(self, obj) -> dict:
        from .metrics_read import latest_metrics
        return latest_metrics(str(obj.device_id or obj.id))


class AgentRoleStatusSerializer(serializers.ModelSerializer):
    class Meta:
        model = AgentRoleStatus
        fields = ("role_type", "services", "ports", "custom", "collected_at")


class AssignedRoleSerializer(serializers.ModelSerializer):
    """A role assigned to a server, with the latest role-check pass/total counts."""
    role_id = serializers.IntegerField(source="role.id", read_only=True)
    role_type = serializers.CharField(source="role.role_type", read_only=True)
    name = serializers.CharField(source="role.name", read_only=True)
    description = serializers.CharField(source="role.description", read_only=True)
    assigned_at = serializers.DateTimeField(source="created_at", read_only=True)
    status = serializers.SerializerMethodField()

    class Meta:
        model = AgentRole
        fields = ("id", "role_id", "role_type", "name", "description",
                  "auto_detected", "assigned_at", "status")

    def get_status(self, obj) -> dict | None:
        st = AgentRoleStatus.objects.filter(
            agent_id=obj.agent_id, role_type=obj.role.role_type).first()
        if not st:
            return None
        services = st.services or []
        ports = st.ports or []
        ok = (sum(1 for s in services if isinstance(s, dict) and s.get("running"))
              + sum(1 for p in ports if isinstance(p, dict) and p.get("open")))
        total = len(services) + len(ports)
        return {
            "checks_passed": ok, "checks_total": total,
            "services": services, "ports": ports,
            "collected_at": st.collected_at,
        }


class AgentEnrollmentTokenSerializer(serializers.ModelSerializer):
    """Token value is write-once: returned in full only on create, masked after."""
    token = serializers.SerializerMethodField()
    site_name = serializers.CharField(source="site.name", read_only=True, default=None)

    class Meta:
        model = AgentEnrollmentToken
        fields = (
            "id", "token", "description", "target_os", "expires_at", "max_uses",
            "use_count", "site", "site_name", "is_active", "created_at",
        )
        read_only_fields = ("id", "use_count", "created_at")

    def get_token(self, obj) -> str:
        # Full token only immediately after creation; masked on list/retrieve.
        if getattr(obj, "_reveal_token", False):
            return obj.token
        return f"{obj.token[:8]}…" if obj.token else ""


class EnrollRequestSerializer(serializers.Serializer):
    enrollment_token = serializers.CharField()
    hostname = serializers.CharField(max_length=255)
    os = serializers.CharField(max_length=64, required=False, allow_blank=True, default="")
    # OS-detail (additive; older agents omit these → default "").
    os_name = serializers.CharField(max_length=128, required=False, allow_blank=True, default="")
    os_version = serializers.CharField(max_length=64, required=False, allow_blank=True, default="")
    kernel = serializers.CharField(max_length=128, required=False, allow_blank=True, default="")
    arch = serializers.CharField(max_length=32, required=False, allow_blank=True, default="")
    version = serializers.CharField(max_length=32, required=False, allow_blank=True, default="")
    csr = serializers.CharField()
