"""
RBAC capability catalog (Track 2).

Capabilities are FIXED IN CODE — the closed set of fine-grained permissions the
platform gates on. Roles (``apps.core.models.RBACRole``) are data: each role
holds a subset of these strings. Nothing creates capabilities at runtime; a
role's ``capabilities`` are validated against :data:`ALL_CAPABILITIES`.

Phase A only DEFINES the catalog and seeds the five system roles to reproduce
today's behavior exactly — no viewset is migrated to capability checks yet (that
is Phase B). The legacy ``Role`` TextChoices + ``NetPulsePermission`` /
``AdminOnly`` classes remain the live enforcement.
"""
from __future__ import annotations

# ── Operational: device / alert / telemetry / circuit / report / log ──────────
DEVICE_VIEW = "device:view"
DEVICE_EDIT = "device:edit"
ALERT_VIEW = "alert:view"
ALERT_MANAGE = "alert:manage"
TELEMETRY_VIEW = "telemetry:view"
CIRCUIT_VIEW = "circuit:view"
CIRCUIT_EDIT = "circuit:edit"
REPORT_VIEW = "report:view"
REPORT_GENERATE = "report:generate"
LOG_VIEW = "log:view"

# ── Config & compliance ───────────────────────────────────────────────────────
CONFIG_PUSH = "config:push"
CONFIG_TEMPLATE_EDIT = "config:template:edit"
CONFIG_BACKUP_MANAGE = "config:backup:manage"
COMPLIANCE_RUN = "compliance:run"
COMPLIANCE_TEMPLATE_EDIT = "compliance:template:edit"

# ── CVE & lifecycle ───────────────────────────────────────────────────────────
CVE_VIEW = "cve:view"
CVE_MANAGE = "cve:manage"
LIFECYCLE_VIEW = "lifecycle:view"

# ── Platform / infrastructure management (admin-only today) ───────────────────
CREDENTIAL_MANAGE = "credential:manage"
INTEGRATION_MANAGE = "integration:manage"
TLS_MANAGE = "tls:manage"
BACKUP_MANAGE = "backup:manage"
MIB_MANAGE = "mib:manage"
AGENT_MANAGE = "agent:manage"
COLLECTOR_MANAGE = "collector:manage"

# ── Access control (admin-only today) ─────────────────────────────────────────
USER_MANAGE = "user:manage"
RBAC_MANAGE = "rbac:manage"
SSO_MANAGE = "sso:manage"

# ── ChatOps ───────────────────────────────────────────────────────────────────
CHATOPS_USE = "chatops:use"
CHATOPS_COMMAND = "chatops:command"
CHATOPS_MANAGE = "chatops:manage"


ALL_CAPABILITIES: frozenset[str] = frozenset({
    DEVICE_VIEW, DEVICE_EDIT, ALERT_VIEW, ALERT_MANAGE, TELEMETRY_VIEW,
    CIRCUIT_VIEW, CIRCUIT_EDIT, REPORT_VIEW, REPORT_GENERATE, LOG_VIEW,
    CONFIG_PUSH, CONFIG_TEMPLATE_EDIT, CONFIG_BACKUP_MANAGE, COMPLIANCE_RUN,
    COMPLIANCE_TEMPLATE_EDIT,
    CVE_VIEW, CVE_MANAGE, LIFECYCLE_VIEW,
    CREDENTIAL_MANAGE, INTEGRATION_MANAGE, TLS_MANAGE, BACKUP_MANAGE, MIB_MANAGE,
    AGENT_MANAGE, COLLECTOR_MANAGE,
    USER_MANAGE, RBAC_MANAGE, SSO_MANAGE,
    CHATOPS_USE, CHATOPS_COMMAND, CHATOPS_MANAGE,
})

# Read-only ("…:view") capabilities — the viewer baseline.
VIEW_CAPABILITIES: frozenset[str] = frozenset({
    DEVICE_VIEW, ALERT_VIEW, TELEMETRY_VIEW, CIRCUIT_VIEW, REPORT_VIEW,
    LOG_VIEW, CVE_VIEW, LIFECYCLE_VIEW,
})

# Engineer = operational view+edit + config:push + compliance:run + chatops:use,
# plus the caps backed by currently-IsAuthenticated viewsets that engineers can
# already reach today (report:generate, config:backup:manage). Excludes every
# admin-only *:manage platform/access-control cap and every *:template:edit
# (post-Track-1 reality: engineers lost credential/integration/tls writes and
# template authoring).
ENGINEER_CAPABILITIES: frozenset[str] = frozenset({
    DEVICE_VIEW, DEVICE_EDIT, ALERT_VIEW, ALERT_MANAGE, TELEMETRY_VIEW,
    CIRCUIT_VIEW, CIRCUIT_EDIT, REPORT_VIEW, REPORT_GENERATE, LOG_VIEW,
    CONFIG_PUSH, CONFIG_BACKUP_MANAGE, COMPLIANCE_RUN, CVE_VIEW, LIFECYCLE_VIEW,
    CHATOPS_USE,
})

# The api service-account role mirrors engineer today.
API_CAPABILITIES: frozenset[str] = ENGINEER_CAPABILITIES

# The five seeded system roles, in seed order. superadmin/admin hold every
# capability; superadmin is immutable (can't be deleted or down-scoped).
SYSTEM_ROLES: tuple[dict, ...] = (
    {"name": "superadmin",
     "description": "Unrestricted; cannot be deleted or down-scoped.",
     "capabilities": ALL_CAPABILITIES, "is_system": True, "is_immutable": True},
    {"name": "admin",
     "description": "Full access to every capability (mutable/deletable).",
     "capabilities": ALL_CAPABILITIES, "is_system": True, "is_immutable": False},
    {"name": "engineer",
     "description": "Operational view/edit, config push, compliance runs, ChatOps.",
     "capabilities": ENGINEER_CAPABILITIES, "is_system": True, "is_immutable": False},
    {"name": "api",
     "description": "Service-account access (mirrors engineer).",
     "capabilities": API_CAPABILITIES, "is_system": True, "is_immutable": False},
    {"name": "viewer",
     "description": "Read-only access to all view capabilities.",
     "capabilities": VIEW_CAPABILITIES, "is_system": True, "is_immutable": False},
)

# Maps the legacy ``Role`` TextChoices value → seeded system-role name, used by
# the data migration to populate each existing user's rbac_role FK. Superusers
# keep their legacy mapping (admin → admin); the superadmin role is seeded but
# assigned to no one automatically (superuser bypass covers them).
LEGACY_ROLE_TO_SYSTEM: dict[str, str] = {
    "admin": "admin",
    "engineer": "engineer",
    "viewer": "viewer",
    "api": "api",
}
