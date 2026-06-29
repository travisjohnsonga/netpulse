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

import os
import re
import secrets
import uuid

from django.conf import settings
from django.db import models

from apps.core.models import TimestampedModel

# An agent is "offline" when it hasn't checked in within this window (and is
# ACTIVE). Default 5 min = 10 missed 30s intervals — clearly down, not a blip.
# Configurable globally (env AGENT_OFFLINE_SECONDS) and overridable per-agent via
# Agent.offline_threshold_seconds. The SAME threshold drives both the online
# badge (is_online) and the liveness alert, so they never disagree. 5 minutes
# also matches the Servers page (OFFLINE_MS).
AGENT_OFFLINE_SECONDS = int(os.environ.get("AGENT_OFFLINE_SECONDS", "300"))
# Back-compat alias (older imports referenced AGENT_ONLINE_SECONDS).
AGENT_ONLINE_SECONDS = AGENT_OFFLINE_SECONDS

# Defaults for the operator-editable, agent-pulled desired config. effective_config()
# merges an agent's sparse desired_config over these so every key is always present.
# disk.exclude_mounts / include_mounts let an operator drop volumes (e.g. a
# recovery partition) from collection; exclude wins over include.
DEFAULT_AGENT_CONFIG = {
    "collection": {"cpu": True, "memory": True, "disk": True, "network": True, "services": False},
    "interval_seconds": 30,
    "disk": {"exclude_mounts": [], "include_mounts": []},
    # Log forwarding (Stage 1 = curated SECURITY PROFILE, default-on for Linux:
    # auth/service/kernel logs). additional_paths is an operator escape hatch,
    # constrained to the LOG_PATH_ALLOWLIST_ROOT (see serializers/agent — enforced
    # both sides). The agent tails + ships raw lines; all parsing is server-side.
    "logs": {"security_profile": True, "additional_paths": []},
    # Service stability monitoring (role-INDEPENDENT): operator-chosen services to
    # watch for up/down + restart/flap. The agent runs the EXISTING rich
    # CollectServices() over this list and reports state every check-in; the
    # server tracks transitions + alerts. Names are validated/capped (below).
    "stability": {"services": []},
    # Functional health checks (Stage 1: web). Per-role URL lists the agent GETs
    # on localhost; empty = derive from the role's open ports. SSRF-constrained to
    # the host itself (is_allowed_self_url, both sides).
    "functional": {"web": {"urls": []}},
    # Per-role SERVICE selection: which of a role's defined services THIS server
    # actually runs (e.g. Web → apache2 only, not nginx/httpd). {role_type: [names]}.
    # Empty/absent for a role = count ALL the role's services (current behavior).
    # The role-status count (AssignedRoleSerializer.get_status) honors this subset
    # so an unselected service isn't counted as a failing "not_found".
    "role_services": {},
}

# Functional-check guardrails.
FUNCTIONAL_CERT_WARN_DAYS = int(os.environ.get("FUNCTIONAL_CERT_WARN_DAYS", "30"))
FUNCTIONAL_MAX_URLS = 20
# SSRF allowlist: a functional-check URL must be http(s) to the HOST ITSELF
# (loopback). The agent checks its own site; it must never be pointed at an
# arbitrary internal/external address. Enforced server-side (serializer) AND
# agent-side (defense in depth, incl. redirect re-validation).
_SELF_HOSTS = {"localhost", "127.0.0.1", "::1", "[::1]", "0.0.0.0"}


def is_allowed_self_url(url: str) -> bool:
    from urllib.parse import urlparse
    try:
        u = urlparse((url or "").strip())
    except (ValueError, TypeError):
        return False
    if u.scheme not in ("http", "https") or not u.hostname:
        return False
    return u.hostname.lower() in _SELF_HOSTS

# Watched service-name guardrail: a safe charset (passed to `systemctl show <unit>`
# as an exec arg, not shell — but validated as defense in depth) and a cap so a
# bad/large config can't blow up the agent's per-check-in work.
STABILITY_SERVICE_RE = re.compile(r"^[A-Za-z0-9._@:+-]+$")
STABILITY_MAX_SERVICES = 50
# Flap detection: N restarts within the window → "flapping".
STABILITY_FLAP_WINDOW_S = 600
STABILITY_FLAP_THRESHOLD = 3


def is_valid_service_name(name: str) -> bool:
    name = (name or "").strip()
    return bool(name) and len(name) <= 128 and bool(STABILITY_SERVICE_RE.match(name))

# additional_paths must live under this root (a root agent reading arbitrary
# files would be a file-exfiltration hole). Enforced server-side (serializer) AND
# agent-side (refuse out-of-allowlist even if a bad config arrives).
LOG_PATH_ALLOWLIST_ROOT = "/var/log/"
# Substrings that are rejected even under the allowlist root (defense in depth).
LOG_PATH_DENY_SUBSTRINGS = ("..", "key", "secret", "shadow", "/.ssh/", "private")


def is_allowed_log_path(path: str) -> bool:
    """True if an operator-supplied log path is safe to tail: an absolute path
    under LOG_PATH_ALLOWLIST_ROOT, with no traversal or secret-bearing names.
    The same rule is enforced agent-side (defense in depth)."""
    if not isinstance(path, str) or not path:
        return False
    p = path.strip()
    low = p.lower()
    if any(s in low for s in LOG_PATH_DENY_SUBSTRINGS):
        return False
    return p.startswith(LOG_PATH_ALLOWLIST_ROOT)


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
    # os is the os_family (the agent's runtime.GOOS: "linux"/"windows") — the
    # code branches on it (role checks, config paths). os_name/os_version/
    # os_kernel are ADDITIONAL human-facing detail collected from the host
    # (/etc/os-release PRETTY_NAME, Windows ProductName, uname -r). All blank for
    # agents predating OS-detail, so the serializer/UI fall back to os.
    os = models.CharField(max_length=64, blank=True)
    os_name = models.CharField(max_length=128, blank=True)
    os_version = models.CharField(max_length=64, blank=True)
    os_kernel = models.CharField(max_length=128, blank=True)
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
    # The agent's REAL source IP as seen on its last metrics check-in (via
    # get_client_ip — spoof-resistant X-Forwarded-For). This is the host's real
    # network address, used for collector-originated reachability (ping/RTT) —
    # distinct from the linked Device's ip_address, which for agents is often a
    # synthetic placeholder (#118 self-heal). Null until a usable client IP is
    # seen; a loopback/placeholder value means "not network-probeable".
    last_ip = models.GenericIPAddressField(null=True, blank=True)
    # Liveness alerting (see apps/agents/liveness.py). offline_threshold_seconds
    # overrides the global AGENT_OFFLINE_SECONDS for THIS agent (null = global) —
    # tighten for a critical host. liveness_alerts_enabled=False suppresses the
    # offline alert entirely (e.g. a lab box that legitimately sleeps when idle,
    # so the napping host doesn't alert-storm).
    offline_threshold_seconds = models.PositiveIntegerField(null=True, blank=True)
    liveness_alerts_enabled = models.BooleanField(default=True)
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
        logs = cfg.get("logs") or {}
        stability = cfg.get("stability") or {}
        return {
            "collection": {**DEFAULT_AGENT_CONFIG["collection"], **(cfg.get("collection") or {})},
            "interval_seconds": cfg.get("interval_seconds") or self.collection_interval
            or DEFAULT_AGENT_CONFIG["interval_seconds"],
            "disk": {
                "exclude_mounts": list(disk.get("exclude_mounts") or []),
                "include_mounts": list(disk.get("include_mounts") or []),
            },
            "logs": {
                "security_profile": logs.get("security_profile",
                                            DEFAULT_AGENT_CONFIG["logs"]["security_profile"]),
                "additional_paths": list(logs.get("additional_paths") or []),
            },
            "stability": {"services": list(stability.get("services") or [])},
            "functional": {"web": {"urls": list(
                ((cfg.get("functional") or {}).get("web") or {}).get("urls") or [])}},
            "role_services": {str(k): list(v or []) for k, v in
                              (cfg.get("role_services") or {}).items()},
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

    def offline_after_seconds(self) -> int:
        """Effective offline threshold for this agent: the per-agent override if
        set, else the global AGENT_OFFLINE_SECONDS. Used by BOTH the online badge
        (is_online) and the liveness alert so they stay consistent."""
        return self.offline_threshold_seconds or AGENT_OFFLINE_SECONDS

    @property
    def is_online(self) -> bool:
        """True when the agent is ACTIVE and checked in within its effective
        offline threshold. Mirrors Collector.is_healthy and the Servers page."""
        if self.status != self.Status.ACTIVE or not self.last_seen:
            return False
        from django.utils import timezone
        return (timezone.now() - self.last_seen).total_seconds() < self.offline_after_seconds()


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
    # Functional health-check results (Stage 1: web HTTP+cert). Per-URL dicts
    # {url, health, status_code, latency_ms, cert_days_remaining, error}. Distinct
    # from `custom` (user-defined checks); additive/nullable for backward-compat.
    functional = models.JSONField(default=list, blank=True)
    collected_at = models.DateTimeField(null=True, blank=True)

    class Meta(TimestampedModel.Meta):
        unique_together = [("agent", "role_type")]
        ordering = ["role_type"]

    def __str__(self):
        return f"{self.agent_id}/{self.role_type}"


class WatchedServiceStatus(TimestampedModel):
    """Stability state for one operator-watched service on one agent (role-
    INDEPENDENT). Updated each check-in from the agent's rich ServiceStat; tracks
    transitions (down/restart) so down + flap alerts can debounce/auto-resolve.
    `restarts` holds recent restart timestamps (ISO, trimmed to 24h) — the source
    for the 24h restart count and the flap-window check."""

    agent = models.ForeignKey(Agent, on_delete=models.CASCADE, related_name="watched_services")
    name = models.CharField(max_length=128)
    running = models.BooleanField(default=False)
    state = models.CharField(max_length=32, blank=True)   # active/inactive/not_found/…
    last_change_at = models.DateTimeField(null=True, blank=True)  # last running flip
    down_since = models.DateTimeField(null=True, blank=True)      # set on →stopped
    restarts = models.JSONField(default=list)                     # recent restart ISO ts (≤24h)
    collected_at = models.DateTimeField(null=True, blank=True)

    class Meta(TimestampedModel.Meta):
        unique_together = [("agent", "name")]
        ordering = ["name"]

    def __str__(self):
        return f"{self.agent_id}/{self.name}"
