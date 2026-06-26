"""
NetPulse Agent — lightweight server-monitoring agents.

An ``Agent`` is a Go process running on a Linux/Windows server that enrolls
(via a one-time ``AgentEnrollmentToken``), receives an OpenBao-issued client
certificate, and pushes metrics + role-check results over mTLS. Each agent is
linked to a ``devices.Device`` so agent-monitored servers appear in inventory
alongside SNMP/REST devices. ``ServerRole`` profiles define which services /
ports / custom checks a role-tagged agent should monitor.
"""
from __future__ import annotations

import secrets
import uuid

from django.conf import settings
from django.db import models

from apps.core.models import TimestampedModel

# An agent is "online" when it last checked in within this window (and is ACTIVE).
# 5 minutes matches the Servers page (OFFLINE_MS); cf. collectors'
# HEARTBEAT_HEALTHY_SECONDS for the equivalent collector concept.
AGENT_ONLINE_SECONDS = 300

# Defaults for the operator-editable, agent-pulled desired config. effective_config()
# merges an agent's sparse desired_config over these so every key is always present.
# disk.exclude_mounts / include_mounts let an operator drop volumes (e.g. a
# recovery partition) from collection; exclude wins over include.
DEFAULT_AGENT_CONFIG = {
    "collection": {"cpu": True, "memory": True, "disk": True, "network": True, "services": False},
    "interval_seconds": 30,
    "disk": {"exclude_mounts": [], "include_mounts": []},
}


def _generate_token() -> str:
    return secrets.token_urlsafe(32)


class AgentEnrollmentToken(TimestampedModel):
    """One-time (or N-use) token an admin generates to enroll agents."""

    class TargetOS(models.TextChoices):
        LINUX = "linux", "Linux"
        WINDOWS = "windows", "Windows"
        ANY = "any", "Any"

    token = models.CharField(max_length=64, unique=True, default=_generate_token, db_index=True)
    description = models.CharField(max_length=255, blank=True)
    # Informational: which OS the install command in the UI targets. Does NOT
    # restrict which OS can actually enroll with the token.
    target_os = models.CharField(max_length=20, choices=TargetOS.choices, default=TargetOS.ANY)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="agent_tokens",
    )
    expires_at = models.DateTimeField(null=True, blank=True)
    max_uses = models.IntegerField(default=1, help_text="0 = unlimited")
    use_count = models.IntegerField(default=0)
    site = models.ForeignKey(
        "devices.Site", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="agent_tokens",
    )
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.description or self.token[:12]

    def is_valid(self) -> bool:
        from django.utils import timezone
        if not self.is_active:
            return False
        if self.max_uses and self.use_count >= self.max_uses:
            return False
        if self.expires_at and timezone.now() > self.expires_at:
            return False
        return True


class ServerRole(TimestampedModel):
    """A server-role profile: the services/ports/custom checks to monitor."""

    class RoleType(models.TextChoices):
        DHCP = "dhcp", "DHCP Server"
        DNS = "dns", "DNS Server"
        NPS = "nps", "Network Policy Server"
        DC = "dc", "Domain Controller"
        WEB = "web", "Web Server"
        DB = "db", "Database Server"
        FILE = "file", "File Server"
        PRINT = "print", "Print Server"
        SYSLOG = "syslog", "Syslog Server"
        MONITORING = "monitoring", "Monitoring Server"
        CUSTOM = "custom", "Custom"

    name = models.CharField(max_length=128)
    role_type = models.CharField(max_length=32, choices=RoleType.choices, db_index=True)
    description = models.TextField(blank=True)
    windows_services = models.JSONField(default=list, help_text="Windows service names to monitor")
    linux_services = models.JSONField(default=list, help_text="systemd unit names to monitor")
    port_checks = models.JSONField(default=list, help_text='[{"port": 53, "proto": "udp", "name": "DNS"}]')
    custom_checks = models.JSONField(default=list)
    is_builtin = models.BooleanField(default=False)

    class Meta(TimestampedModel.Meta):
        ordering = ["name"]

    def __str__(self):
        return self.name


class Agent(TimestampedModel):
    """A server-monitoring agent and its enrollment/certificate state."""

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        INACTIVE = "inactive", "Inactive"
        REVOKED = "revoked", "Revoked"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    hostname = models.CharField(max_length=255, db_index=True)
    device = models.OneToOneField(
        "devices.Device", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="agent",
    )
    os = models.CharField(max_length=64, blank=True)
    arch = models.CharField(max_length=32, blank=True)
    version = models.CharField(max_length=32, blank=True)
    cert_serial = models.CharField(max_length=128, blank=True, db_index=True)
    # Canonicalized form of cert_serial (separators stripped, uppercased) so the
    # nginx-forwarded mTLS serial can be matched with a single indexed lookup
    # instead of scanning + normalizing every agent in Python on each request.
    # Kept in sync by save(); see AgentCertAuthentication.
    cert_serial_normalized = models.CharField(max_length=128, blank=True, db_index=True)
    cert_expires_at = models.DateTimeField(null=True, blank=True)
    enrollment_token = models.ForeignKey(
        AgentEnrollmentToken, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="agents",
    )
    # Role assignments go through AgentRole (assignment metadata); reverse
    # accessor on the agent is `assigned_roles`, on the role is `agent_assignments`.
    server_roles = models.ManyToManyField(
        ServerRole, through="AgentRole", related_name="agents", blank=True,
    )
    # Names of services reported running by the agent's latest metrics push;
    # used by role auto-detection. Populated only when the agent collects services.
    reported_services = models.JSONField(default=list, blank=True)
    last_seen = models.DateTimeField(null=True, blank=True, db_index=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE, db_index=True)
    collection_interval = models.IntegerField(default=30, help_text="seconds")
    # Operator-set DESIRED config the agent pulls on its next check-in (the
    # metrics-push response carries it; the agent applies + rewrites its local
    # config.json). Stored sparse — effective_config() merges it over the
    # defaults so the agent/UI always see a complete config. Pull model only:
    # there is no inbound channel to the agent.
    desired_config = models.JSONField(default=dict, blank=True)

    def effective_config(self) -> dict:
        """The desired config merged over DEFAULT_AGENT_CONFIG, so callers always
        get every key. interval falls back to the legacy collection_interval."""
        cfg = self.desired_config or {}
        disk = cfg.get("disk") or {}
        return {
            "collection": {**DEFAULT_AGENT_CONFIG["collection"], **(cfg.get("collection") or {})},
            "interval_seconds": cfg.get("interval_seconds") or self.collection_interval
            or DEFAULT_AGENT_CONFIG["interval_seconds"],
            "disk": {
                "exclude_mounts": list(disk.get("exclude_mounts") or []),
                "include_mounts": list(disk.get("include_mounts") or []),
            },
        }

    def save(self, *args, **kwargs):
        # Keep the normalized serial in lockstep with cert_serial so the
        # authenticator can resolve an agent with one indexed query.
        from .authentication import normalize_serial
        self.cert_serial_normalized = normalize_serial(self.cert_serial)
        update_fields = kwargs.get("update_fields")
        if update_fields is not None and "cert_serial" in update_fields:
            kwargs["update_fields"] = list(update_fields) + ["cert_serial_normalized"]
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.hostname} ({self.id})"

    # An mTLS-authenticated agent is the request principal (request.user) for
    # metrics/role-check ingestion, so DRF's IsAuthenticated must treat it as
    # authenticated. It is never a Django auth user — admin endpoints still use
    # JWT — so these are simple constants.
    @property
    def is_authenticated(self) -> bool:
        return True

    @property
    def is_anonymous(self) -> bool:
        return False

    @property
    def is_online(self) -> bool:
        """True when the agent is ACTIVE and checked in within AGENT_ONLINE_SECONDS.
        Mirrors Collector.is_healthy and the Servers page's online logic."""
        if self.status != self.Status.ACTIVE or not self.last_seen:
            return False
        from django.utils import timezone
        return (timezone.now() - self.last_seen).total_seconds() < AGENT_ONLINE_SECONDS


class AgentRole(TimestampedModel):
    """A server role assigned to an agent (the M2M `through`). Carries who/how it
    was assigned. `created_at` (from TimestampedModel) is the assignment time."""

    agent = models.ForeignKey(Agent, on_delete=models.CASCADE, related_name="assigned_roles")
    role = models.ForeignKey(ServerRole, on_delete=models.CASCADE, related_name="agent_assignments")
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="+",
    )
    # True when created by auto-detection / agent config rather than a manual UI assign.
    auto_detected = models.BooleanField(default=False)

    class Meta(TimestampedModel.Meta):
        unique_together = [("agent", "role")]
        ordering = ["role__name"]

    def __str__(self):
        return f"{self.agent.hostname} → {self.role.name}"


class AgentRoleStatus(TimestampedModel):
    """Latest role-check result for one (agent, role_type) — services/ports/custom."""

    agent = models.ForeignKey(Agent, on_delete=models.CASCADE, related_name="role_statuses")
    role_type = models.CharField(max_length=32)
    services = models.JSONField(default=list)
    ports = models.JSONField(default=list)
    custom = models.JSONField(default=list)
    collected_at = models.DateTimeField(null=True, blank=True)

    class Meta(TimestampedModel.Meta):
        unique_together = [("agent", "role_type")]
        ordering = ["role_type"]

    def __str__(self):
        return f"{self.agent_id}/{self.role_type}"
