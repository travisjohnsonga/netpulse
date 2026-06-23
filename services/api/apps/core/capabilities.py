"""
RBAC capability catalog (Track 2).

Capabilities are FIXED IN CODE — the closed set of fine-grained permissions the
platform gates on. Roles (``apps.core.models.RBACRole``) are data: each role
holds a subset of these strings, validated against :data:`ALL_CAPABILITIES`.

Phase B migrates every viewset onto ``HasCapability`` and flips the DRF default to
deny-by-default. The catalog is EXTENDED beyond the Phase-A 31 with the granular
caps needed to express today's per-viewset access (the Phase-A set was coarser
than the real permission map). Convention: coarse ``:view`` / ``:edit`` per domain
(``:edit`` = create+update+delete); ``:manage`` for a platform/config surface
(admin) — except ``check:manage``, which gates operational service-check config
(engineer). Every new cap is justified by a real viewset (see the PR table). The
seeded role sets below are the behavior-preservation oracle; the only intended
behavior change is config:backup:manage → admin-only.
"""
from __future__ import annotations

# ── Operational: device / alert / telemetry / circuit / report / log ──────────
DEVICE_VIEW = "device:view"
DEVICE_EDIT = "device:edit"
ALERT_VIEW = "alert:view"
ALERT_MANAGE = "alert:manage"
TELEMETRY_VIEW = "telemetry:view"
TELEMETRY_EDIT = "telemetry:edit"             # (new) telemetry/interface config edits
CIRCUIT_VIEW = "circuit:view"
CIRCUIT_EDIT = "circuit:edit"
REPORT_VIEW = "report:view"
REPORT_GENERATE = "report:generate"
LOG_VIEW = "log:view"
LOG_EDIT = "log:edit"                          # (new) log-filter CRUD

# ── Config & compliance ───────────────────────────────────────────────────────
CONFIG_PUSH = "config:push"
CONFIG_TEMPLATE_EDIT = "config:template:edit"
CONFIG_BACKUP_MANAGE = "config:backup:manage"  # device-config backup mgmt (now admin)
COMPLIANCE_VIEW = "compliance:view"            # (new) policy/rule reads
COMPLIANCE_EDIT = "compliance:edit"            # (new) policy/rule CRUD
COMPLIANCE_RUN = "compliance:run"
COMPLIANCE_TEMPLATE_EDIT = "compliance:template:edit"

# ── Checks (service monitors — operational) ───────────────────────────────────
CHECK_VIEW = "check:view"                      # (new)
CHECK_MANAGE = "check:manage"                  # (new) engineer manages checks

# ── CVE & lifecycle ───────────────────────────────────────────────────────────
CVE_VIEW = "cve:view"
CVE_TRIAGE = "cve:triage"                      # (new) per-device CVE status update
CVE_MANAGE = "cve:manage"                      # feed config (admin)
LIFECYCLE_VIEW = "lifecycle:view"
LIFECYCLE_EDIT = "lifecycle:edit"              # (new) milestone CRUD

# ── Flow analytics ────────────────────────────────────────────────────────────
FLOW_VIEW = "flow:view"                        # (new) read flow analytics
FLOW_MANAGE = "flow:manage"                    # (new) admin maintenance (cache clear)

# ── Regulatory frameworks ─────────────────────────────────────────────────────
FRAMEWORK_VIEW = "framework:view"              # (new) read regulatory frameworks

# ── Platform / infrastructure: view (all-auth today) + manage (admin) ─────────
# A handful of operational actions in these admin-managed domains are run by
# ENGINEERS today (via the permissive default), not just admins. To preserve that
# access without granting them the admin :manage cap (configure/secrets) or
# loosening read-only viewers into infra-reaching probes, each gets an engineer-
# tier *operate* cap (see the "access-preserving additions" note in the PR). These
# are preservation, NOT tightenings.
CREDENTIAL_VIEW = "credential:view"            # (new) list credential profiles + see devices
CREDENTIAL_TEST = "credential:test"            # (new, engineer) connectivity-probe a profile (CredentialProfileViewSet.test)
CREDENTIAL_MANAGE = "credential:manage"
INTEGRATION_VIEW = "integration:view"          # (new) read integration config
INTEGRATION_SYNC = "integration:sync"          # (new, engineer) run non-mutating integration ops: test/preview/cloud-discover/email-test
INTEGRATION_MANAGE = "integration:manage"
TLS_VIEW = "tls:view"                          # (new) cert status / CA list reads
TLS_VERIFY = "tls:verify"                      # (new, engineer) verify a stored CA cert's validity (CACertificateVerifyView)
TLS_MANAGE = "tls:manage"
MIB_VIEW = "mib:view"                          # (new) list/resolve MIBs
MIB_MANAGE = "mib:manage"
AGENT_VIEW = "agent:view"                      # (new) servers/agents read
AGENT_EDIT = "agent:edit"                      # (new) provision agents: enroll/revoke + server-role writes (engineer)
AGENT_MANAGE = "agent:manage"
COLLECTOR_VIEW = "collector:view"              # (new) read collectors
COLLECTOR_MANAGE = "collector:manage"
BACKUP_VIEW = "backup:view"                    # (new) platform-backup reads/download
BACKUP_MANAGE = "backup:manage"                # platform backup run/config (admin)

# ── Access control & system (admin-only) ─────────────────────────────────────
USER_MANAGE = "user:manage"
RBAC_MANAGE = "rbac:manage"                    # roles + audit log
SSO_MANAGE = "sso:manage"
SYSTEM_MANAGE = "system:manage"                # (new) hostname/LLDP/system settings

# ── ChatOps ───────────────────────────────────────────────────────────────────
CHATOPS_USE = "chatops:use"                    # in-UI chat (all authenticated)
CHATOPS_COMMAND = "chatops:command"            # action commands (Phase 4+)
CHATOPS_MANAGE = "chatops:manage"              # platform/channel/identity config


ALL_CAPABILITIES: frozenset[str] = frozenset({
    DEVICE_VIEW, DEVICE_EDIT, ALERT_VIEW, ALERT_MANAGE, TELEMETRY_VIEW,
    TELEMETRY_EDIT, CIRCUIT_VIEW, CIRCUIT_EDIT, REPORT_VIEW, REPORT_GENERATE,
    LOG_VIEW, LOG_EDIT,
    CONFIG_PUSH, CONFIG_TEMPLATE_EDIT, CONFIG_BACKUP_MANAGE, COMPLIANCE_VIEW,
    COMPLIANCE_EDIT, COMPLIANCE_RUN, COMPLIANCE_TEMPLATE_EDIT,
    CHECK_VIEW, CHECK_MANAGE,
    CVE_VIEW, CVE_TRIAGE, CVE_MANAGE, LIFECYCLE_VIEW, LIFECYCLE_EDIT,
    FLOW_VIEW, FLOW_MANAGE, FRAMEWORK_VIEW,
    CREDENTIAL_VIEW, CREDENTIAL_TEST, CREDENTIAL_MANAGE,
    INTEGRATION_VIEW, INTEGRATION_SYNC, INTEGRATION_MANAGE,
    TLS_VIEW, TLS_VERIFY, TLS_MANAGE, MIB_VIEW, MIB_MANAGE, AGENT_VIEW,
    AGENT_EDIT, AGENT_MANAGE, COLLECTOR_VIEW, COLLECTOR_MANAGE, BACKUP_VIEW,
    BACKUP_MANAGE,
    USER_MANAGE, RBAC_MANAGE, SSO_MANAGE, SYSTEM_MANAGE,
    CHATOPS_USE, CHATOPS_COMMAND, CHATOPS_MANAGE,
})

# Viewer = every read/view-tier capability the viewer role reaches today (DEFAULT
# safe-method reads + IsAuthenticated read endpoints), plus the in-UI ChatOps chat
# (chatops:use — "for everyone", was IsAuthenticated). No edits/manage.
VIEW_CAPABILITIES: frozenset[str] = frozenset({
    DEVICE_VIEW, ALERT_VIEW, TELEMETRY_VIEW, CIRCUIT_VIEW, REPORT_VIEW, LOG_VIEW,
    CVE_VIEW, LIFECYCLE_VIEW, COMPLIANCE_VIEW, CHECK_VIEW, FRAMEWORK_VIEW,
    FLOW_VIEW, CREDENTIAL_VIEW, INTEGRATION_VIEW, TLS_VIEW, MIB_VIEW, AGENT_VIEW,
    COLLECTOR_VIEW, BACKUP_VIEW, CHATOPS_USE,
})

# Engineer = viewer's read tier + the operational write/edit caps it has today.
# Excludes admin-only *:manage platform/access caps, *:template:edit, cve:manage,
# flow:manage, system:manage, config:backup:manage (Phase-B tightening), and
# chatops:command/manage. check:manage is operational, so engineer HAS it.
# agent:edit is engineer-tier: senior engineers provision agents (enroll/revoke +
# server-role writes), distinct from admin-only agent:manage. The three *operate*
# caps (credential:test, integration:sync, tls:verify) preserve engineer access to
# operational actions that engineers run today in otherwise admin-managed domains.
ENGINEER_CAPABILITIES: frozenset[str] = VIEW_CAPABILITIES | frozenset({
    DEVICE_EDIT, ALERT_MANAGE, CIRCUIT_EDIT, REPORT_GENERATE, CONFIG_PUSH,
    COMPLIANCE_RUN, COMPLIANCE_EDIT, CHECK_MANAGE, TELEMETRY_EDIT, LOG_EDIT,
    LIFECYCLE_EDIT, CVE_TRIAGE, AGENT_EDIT,
    CREDENTIAL_TEST, INTEGRATION_SYNC, TLS_VERIFY,
})

# The api service-account role mirrors engineer.
API_CAPABILITIES: frozenset[str] = ENGINEER_CAPABILITIES

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
     "description": "Read-only access to all view capabilities + in-UI ChatOps.",
     "capabilities": VIEW_CAPABILITIES, "is_system": True, "is_immutable": False},
)

LEGACY_ROLE_TO_SYSTEM: dict[str, str] = {
    "admin": "admin",
    "engineer": "engineer",
    "viewer": "viewer",
    "api": "api",
}
