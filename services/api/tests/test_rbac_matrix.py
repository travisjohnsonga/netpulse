"""Systematic RBAC permission matrix.

Proves every mapped capability-gated endpoint enforces its SPECIFIC capability:
  - a user WITH the capability passes the gate,
  - a user WITH a different capability (authenticated, but not THIS cap) → 403
    (catches the "any authenticated user" trap),
  - an unauthenticated caller → 401.

Plus a COMPLETENESS gate: every capability in ALL_CAPABILITIES must be either
in the matrix or in DEFERRED (with a reason), so a newly-added capability forces
a decision here instead of silently going untested.

Builds users with an EXACT capability set via a real (non-system) RBACRole, so
it exercises the real has_capability() resolution path, not a mock.
"""
import itertools

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from apps.core import capabilities as caps
from apps.core.models import RBACRole

User = get_user_model()
pytestmark = pytest.mark.django_db

_seq = itertools.count()


def client_with_caps(capset):
    """An authenticated APIClient for a user whose role grants exactly capset."""
    n = next(_seq)
    role = RBACRole.objects.create(
        name=f"matrix-{n}", capabilities=sorted(capset), is_system=False)
    u = User.objects.create_user(username=f"matrix-{n}", password="x", role="viewer")
    u.rbac_role = role
    u._rbac_role_explicit = True  # keep the custom role; don't re-derive from `role`
    u.save()
    c = APIClient()
    c.credentials(HTTP_AUTHORIZATION=f"Bearer {RefreshToken.for_user(u).access_token}")
    return c


# (capability, method, url, kind) — kind "read" expects 200 with the cap; "write"
# expects the request to PASS the permission gate (status not in 401/403; the body
# may 400/201/etc.). URLs are list endpoints so no per-object fixture is needed.
MATRIX = [
    ("device:view",     "get",  "/api/devices/",          "read"),
    ("device:edit",     "post", "/api/devices/",          "write"),
    ("device:view",     "get",  "/api/sites/",            "read"),   # SiteViewSet
    ("circuit:view",    "get",  "/api/circuits/",         "read"),
    ("circuit:edit",    "post", "/api/circuits/",         "write"),
    ("check:view",      "get",  "/api/checks/",           "read"),
    ("check:manage",    "post", "/api/checks/",           "write"),
    ("collector:view",  "get",  "/api/collectors/",       "read"),
    ("collector:manage", "post", "/api/collectors/",      "write"),
    ("alert:view",      "get",  "/api/alerting/teams/",   "read"),
    ("alert:manage",    "post", "/api/alerting/teams/",   "write"),
    ("lifecycle:view",  "get",  "/api/lifecycle/milestones/", "read"),
    ("lifecycle:edit",  "post", "/api/lifecycle/milestones/", "write"),
    ("agent:view",      "get",  "/api/servers/",          "read"),
    ("agent:view",      "get",  "/api/agents/tokens/",    "read"),
    ("agent:edit",      "post", "/api/agents/tokens/",    "write"),
    ("credential:view", "get",  "/api/credentials/",      "read"),
    ("cve:view",        "get",  "/api/cve/cves/",         "read"),
    ("log:view",        "get",  "/api/logs/filters/",     "read"),
    ("report:view",     "get",  "/api/reports/",          "read"),
    ("mib:view",        "get",  "/api/mibs/",             "read"),
    ("framework:view",  "get",  "/api/frameworks/",       "read"),
    ("flow:view",       "get",  "/api/flows/",            "read"),
    ("backup:view",     "get",  "/api/backup/records/",   "read"),
    ("user:manage",     "get",  "/api/users/",            "read"),
    ("rbac:manage",     "get",  "/api/rbac/roles/",       "read"),
]

# Capabilities not in the matrix, each with a reason. Keeps the completeness gate
# honest: every ALL_CAPABILITIES member is either tested above or listed here.
DEFERRED = {
    "telemetry:view": "device-scoped action endpoints (/api/devices/{id}/...)",
    "telemetry:edit": "device-scoped action endpoints",
    "config:push": "device-scoped push action, needs a device + ALLOW_CONFIG_PUSH",
    "config:template:edit": "config-template CRUD, action-level",
    "config:backup:manage": "device-config backup actions",
    "compliance:view": "policy/rule + device-scoped actions (covered by app tests)",
    "compliance:edit": "policy/rule CRUD, app-test covered",
    "compliance:run": "run action endpoint",
    "compliance:template:edit": "template CRUD action",
    "cve:triage": "per-device CVE status update (object-scoped)",
    "cve:manage": "feed-config action (get_permissions override)",
    "credential:test": "connectivity-probe action on a profile",
    "credential:manage": "credential write actions (object/secret setup)",
    "integration:view": "integration config reads (per-integration)",
    "integration:sync": "non-mutating integration ops (test/sync actions)",
    "integration:manage": "integration config writes",
    "tls:view": "cert/CA status reads (settings sub-routes)",
    "tls:verify": "verify-CA action",
    "tls:manage": "cert/CA management actions",
    "mib:manage": "MIB upload/delete action",
    "agent:manage": "defined but unused — agent ops use agent:edit (Phase 2+)",
    "backup:manage": "platform backup run/config (destructive; setup-heavy)",
    "sso:manage": "SSO provider config (secret setup); list is public",
    "system:manage": "system-settings PUT endpoints",
    "report:generate": "report-generation action (format/body heavy)",
    "log:edit": "log-filter CRUD write (covered by read gate + app tests)",
    "flow:manage": "admin cache-clear action",
    "chatops:use": "in-UI chat (all authenticated); covered by chatops tests",
    "chatops:command": "Phase 4+ action commands — not built",
    "chatops:manage": "chatops platform/channel config",
}

_OTHER = "report:view"  # a benign cap the 'without' user holds instead of the tested one


def _request(client, method, url):
    return getattr(client, method)(url, {}, format="json") if method == "post" \
        else getattr(client, method)(url)


@pytest.mark.parametrize("cap,method,url,kind", MATRIX, ids=[f"{m[0]}:{m[1]}" for m in MATRIX])
def test_matrix(cap, method, url, kind):
    other = "device:view" if cap == _OTHER else _OTHER

    # Unauthenticated → 401.
    assert _request(APIClient(), method, url).status_code == 401, f"anon {method} {url}"

    # Authenticated but WITHOUT this cap (holds a different one) → 403.
    r_without = _request(client_with_caps({other}), method, url)
    assert r_without.status_code == 403, (
        f"{method} {url} accepted a user lacking {cap} (got {r_without.status_code}) "
        f"— gate may accept any-authenticated instead of the specific cap")

    # WITH the cap → passes the gate.
    r_with = _request(client_with_caps({cap}), method, url)
    if kind == "read":
        assert r_with.status_code == 200, f"{cap} GET {url} → {r_with.status_code}"
    else:
        assert r_with.status_code not in (401, 403), (
            f"{cap} {method} {url} → {r_with.status_code} (cap should pass the gate)")


def test_completeness_every_capability_is_mapped_or_deferred():
    """A capability in ALL_CAPABILITIES with neither a matrix test nor a DEFERRED
    reason fails here — so adding a capability forces adding its coverage."""
    mapped = {m[0] for m in MATRIX}
    accounted = mapped | set(DEFERRED)
    missing = set(caps.ALL_CAPABILITIES) - accounted
    assert not missing, (
        f"capabilities with no matrix test and no DEFERRED reason: {sorted(missing)}")
    # DEFERRED must not reference unknown/removed capabilities.
    stale = set(DEFERRED) - set(caps.ALL_CAPABILITIES)
    assert not stale, f"DEFERRED references unknown capabilities: {sorted(stale)}"


def test_coverage_report(capsys):
    """Prints the coverage summary (X of Y capabilities matrix-tested)."""
    mapped = {m[0] for m in MATRIX}
    total = len(caps.ALL_CAPABILITIES)
    with capsys.disabled():
        print(f"\nRBAC matrix coverage: {len(mapped)}/{total} capabilities directly "
              f"matrix-tested; {len(DEFERRED)} deferred (documented).")
    assert mapped <= set(caps.ALL_CAPABILITIES)


# ── Edge: read/write separation + privilege-escalation ────────────────────────

class TestEdgeCases:
    def test_read_cap_cannot_write(self):
        """agent:view can GET but NOT POST (write needs agent:edit)."""
        c = client_with_caps({"agent:view"})
        assert c.get("/api/agents/tokens/").status_code == 200
        assert c.post("/api/agents/tokens/", {}, format="json").status_code == 403

    def test_write_cap_can_read_and_write(self):
        c = client_with_caps({"agent:view", "agent:edit"})
        assert c.get("/api/agents/tokens/").status_code == 200
        assert c.post("/api/agents/tokens/", {}, format="json").status_code not in (401, 403)

    def test_cannot_self_create_user_without_user_manage(self):
        """A user without user:manage can't create users (no self-escalation)."""
        c = client_with_caps({"device:view"})
        assert c.post("/api/users/", {"username": "x", "password": "y"},
                      format="json").status_code == 403

    def test_cannot_manage_roles_without_rbac_manage(self):
        c = client_with_caps({"device:view"})
        assert c.post("/api/rbac/roles/", {"name": "evil", "capabilities": []},
                      format="json").status_code == 403
