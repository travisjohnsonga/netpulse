from rest_framework import serializers

from .models import Agent, AgentEnrollmentToken, AgentRole, AgentRoleStatus, ServerRole


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

    class Meta:
        model = Agent
        fields = (
            "id", "hostname", "device_id", "site_name", "os", "arch", "version",
            "cert_serial", "cert_expires_at", "status", "collection_interval",
            "role_types", "last_seen", "created_at",
        )
        read_only_fields = fields

    def get_role_types(self, obj) -> list[str]:
        return list(obj.server_roles.values_list("role_type", flat=True))


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
    arch = serializers.CharField(max_length=32, required=False, allow_blank=True, default="")
    version = serializers.CharField(max_length=32, required=False, allow_blank=True, default="")
    csr = serializers.CharField()
