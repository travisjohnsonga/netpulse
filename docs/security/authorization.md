# Authorization (RBAC)

spane's authorization model is capability-based and deny-by-default. Every API
endpoint is gated on a named capability; roles are sets of capabilities; and an
endpoint that forgets to declare a capability is denied rather than silently
opened. This page describes the model as implemented; for the operator-facing
"how do I create a role and assign it" guide, see **Admin → Access Roles**.

## Deny-by-default

The project default permission class is `DenyByDefault`
(`config/settings/base.py`):

```python
"DEFAULT_PERMISSION_CLASSES": [
    "apps.core.permissions.DenyByDefault",
],
```

`DenyByDefault` (`apps/core/permissions.py`) denies every request except
Django superusers:

```python
class DenyByDefault(BasePermission):
    message = "No capability declared for this endpoint."

    def has_permission(self, request, view) -> bool:
        return bool(getattr(request.user, "is_superuser", False))
```

The consequence is that a view which declares no `permission_classes` /
`get_permissions` **fails closed**. A forgotten capability check can never
accidentally grant viewer-read or engineer-write — every reachable endpoint must
explicitly declare `HasCapability` (or the `CapabilityViewSetMixin`), or sit on
the unauthenticated allowlist (e.g. the token and health endpoints).

## The capability catalog

Capabilities are defined in `apps/core/capabilities.py` as `"domain:action"`
string constants, collected into the `ALL_CAPABILITIES` frozenset. There are
**54 capabilities** spanning these domains:

| Domain | Capabilities |
|--------|-------------|
| agent | `agent:view`, `agent:edit`, `agent:manage` |
| alert | `alert:view`, `alert:manage` |
| backup | `backup:view`, `backup:manage` |
| chatops | `chatops:use`, `chatops:command`, `chatops:manage` |
| check | `check:view`, `check:manage` |
| circuit | `circuit:view`, `circuit:edit` |
| collector | `collector:view`, `collector:manage` |
| compliance | `compliance:view`, `compliance:edit`, `compliance:run`, `compliance:template:edit` |
| config | `config:push`, `config:template:edit`, `config:backup:manage` |
| credential | `credential:view`, `credential:test`, `credential:manage` |
| cve | `cve:view`, `cve:triage`, `cve:manage` |
| device | `device:view`, `device:edit` |
| flow | `flow:view`, `flow:manage` |
| framework | `framework:view` |
| integration | `integration:view`, `integration:sync`, `integration:manage` |
| lifecycle | `lifecycle:view`, `lifecycle:edit` |
| log | `log:view`, `log:edit` |
| mib | `mib:view`, `mib:manage` |
| rbac | `rbac:manage` |
| report | `report:view`, `report:generate` |
| sso | `sso:manage` |
| system | `system:manage` |
| telemetry | `telemetry:view`, `telemetry:edit` |
| tls | `tls:view`, `tls:verify`, `tls:manage` |
| user | `user:manage` |

The catalog is also exposed read-only to the UI, grouped by domain prefix, via
`GET /api/rbac/capabilities/` (`apps/core/rbac_views.py`).

## System roles vs custom roles

Five system roles are defined in `apps/core/capabilities.py` (`SYSTEM_ROLES`) and
seeded into the `RBACRole` table by migrations `0015_seed_rbac_roles` and
`0016_resync_rbac_roles_phase_b`:

| Role | Capabilities | `is_system` | `is_immutable` |
|------|-------------|-------------|----------------|
| `superadmin` | all 54 | yes | **yes** |
| `admin` | all 54 | yes | no |
| `engineer` | 36 (`ENGINEER_CAPABILITIES`) | yes | no |
| `api` | 36 (mirrors engineer) | yes | no |
| `viewer` | 20 (`VIEW_CAPABILITIES`) | yes | no |

`engineer`/`api` hold the view set plus operational write/run capabilities
(e.g. `device:edit`, `config:push`, `compliance:run`, `credential:test`,
`integration:sync`, `tls:verify`). `viewer` holds the `*:view` capabilities plus
`chatops:use`.

The `RBACRole` model (`apps/core/models.py`) stores `name`, `description`, a
`capabilities` JSON list, and the `is_system` / `is_immutable` flags. System
roles are read-only through the API (updates/deletes are rejected); custom roles
are full CRUD, subject to the anti-escalation rule below. Users link to a role
through `NetPulseUser.rbac_role` (a nullable FK).

## Per-endpoint enforcement

`HasCapability` (`apps/core/permissions.py`) is a permission factory bound to a
single capability:

```python
permission_classes = [HasCapability("device:edit")]
```

The capability string is validated against `ALL_CAPABILITIES` at construction, so
a typo fails loudly at import time rather than silently denying everyone at
request time. `CapabilityViewSetMixin` gates a ViewSet by method class
(`view_capability` for safe methods, `write_capability` for unsafe ones); a
viewset that sets neither attribute denies. Capability resolution
(`has_capability` / `capabilities_of`) treats Django superusers as holding
everything, and a user with no `rbac_role` as holding nothing.

## Anti-escalation guardrail

A user can only grant capabilities they themselves hold. This is enforced when
creating or updating a role and when assigning a role to a user.

Role create/update (`apps/core/rbac_views.py`):

```python
def _check_escalation(self, capabilities):
    disallowed = set(capabilities or []) - capabilities_of(self.request.user)
    if disallowed:
        raise PermissionDenied(
            "You cannot grant capabilities you do not hold: "
            f"{sorted(disallowed)}")
```

User role assignment (`UserViewSet.assign_rbac_role`, `apps/core/views.py`)
performs the same `role.capability_set() - capabilities_of(request.user)` check
before applying the change. The frontend mirrors this client-side for UX, but
the server 403 is the real boundary.

## Superadmin immutability and lockout guards

`RBACRole` (`apps/core/models.py`) enforces:

- **No down-scoping** — `save()` raises `ValidationError` if an immutable role
  (superadmin) would lose any capability.
- **No deletion** — `delete()` raises `ValidationError` for an immutable role.

Separately, `UserViewSet` (`apps/core/views.py`) prevents organizational
lockout: you cannot delete your own account, and you cannot delete, demote, or
deactivate the **last active administrator** (`_is_last_admin`).

## Drift guard test

`tests/test_rbac_capabilities.py` is the regression guard that keeps the catalog
and the seeded roles honest:

- `test_all_capabilities_count` asserts `len(ALL_CAPABILITIES) == 54` — adding or
  removing a capability without updating the catalog fails the build.
- `test_exact_capability_sets` asserts each seeded system role's stored
  capabilities exactly match the code-defined set, and that superadmin/admin hold
  the full catalog.

## API and UI surface

- `GET/POST /api/rbac/roles/` and `GET/PATCH/DELETE /api/rbac/roles/{id}/` —
  role CRUD (gated by `rbac:manage`; system roles read-only).
- `GET /api/rbac/capabilities/` — the capability catalog grouped by domain.
- `PATCH /api/users/{id}/rbac-role/` — assign a user's role (anti-escalation
  enforced).
- UI: **Settings → Access Roles** (`/settings/access-roles`), gated on
  `rbac:manage`.
