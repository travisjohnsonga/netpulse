# Access Roles (RBAC)

spane controls who can do what with **role-based access control**. Each user is
assigned one role; a role is a named set of **capabilities**; and every API
action is gated on a specific capability. This page is the operator/admin guide
to using RBAC day to day — creating roles, assigning them, and understanding the
guardrails.

> For the *why* and the security-model details (deny-by-default enforcement, the
> permission classes, the drift-guard test), see the **Security → Authorization
> (RBAC)** page. This page focuses on operating it.

You manage all of this under **Settings → Access Roles** (`/settings/access-roles`).
The screen — and the underlying API — require the `rbac:manage` capability, which
only `admin`/`superadmin` hold by default.

## The five system roles

spane ships five built-in ("system") roles:

| Role | What it can do |
|------|----------------|
| **superadmin** | Everything. Holds all 54 capabilities and **cannot be deleted or reduced**. The break-glass role. |
| **admin** | Everything (all 54 capabilities), but unlike superadmin it is a normal mutable/deletable role. Day-to-day administrators. |
| **engineer** | Operational read **and** write: edit devices, push config, run compliance, manage alerts/checks, edit telemetry/logs/lifecycle, triage CVEs, plus "operate" actions (test credentials, sync integrations, verify TLS) and ChatOps. Cannot manage users, roles, SSO, or system settings. |
| **api** | A service-account role that mirrors **engineer** exactly. Use it for automation/integration tokens. |
| **viewer** | Read-only — every `*:view` capability across the product, plus in-UI ChatOps (`chatops:use`). No writes. |

### System roles are canonical and read-only

The five system roles are defined in code and seeded into the database. You
**cannot edit or delete them** through the UI or API (attempts are rejected). If
you need a different permission mix, create a **custom role** instead. New SSO
users default to the `viewer` role unless their provider sets otherwise.

## Creating a custom role

From **Settings → Access Roles**, create a role with a name, a description, and a
set of capabilities ticked from the catalog. Capabilities are named
`domain:action` and grouped by domain:

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

That is the full catalog — **54 capabilities** in total. The capability picker in
the UI is populated live from `GET /api/rbac/capabilities/`, so it always matches
what the server actually enforces.

A practical pattern: start from the set a system role holds (e.g. viewer's
read-only set) and add the one or two write capabilities a team actually needs —
for example a "NOC operator" role that is viewer + `alert:manage`.

## Assigning a role to a user

In **Settings → Access Roles** (or the Users screen), pick a user and set their
role. Behind the scenes this is:

```
PATCH /api/users/{id}/rbac-role/   { "rbac_role_id": <role id> }
```

Role and capability administration is also available directly via the API:

- `GET/POST /api/rbac/roles/` and `GET/PATCH/DELETE /api/rbac/roles/{id}/` — list
  and manage roles (system roles are read-only).
- `GET /api/rbac/capabilities/` — the capability catalog, grouped by domain.

## The anti-escalation rule

**You can only grant capabilities you yourself hold.** When you create or edit a
role, or assign a role to a user, spane rejects the change if it would hand out
any capability that is not already in your own set. The error names exactly which
capabilities were disallowed.

In practice this means an `engineer` (who lacks `rbac:manage` anyway) can't mint
an all-powerful role, and even a custom administrator scoped to a subset of
capabilities cannot escalate a colleague beyond their own reach. The frontend
greys out capabilities you can't grant, but the real boundary is enforced on the
server.

## Lockout protection

To avoid locking yourself out of the platform, spane will not let you delete your
own account, and it will not let you delete, demote, or deactivate the **last
remaining active administrator**.

## It's all enforced server-side

None of the above relies on the UI hiding buttons. Authorization is
**deny-by-default** in the API: every endpoint declares the capability it
requires, and an endpoint that declares none is denied. A user (or an API token)
calling an endpoint they lack the capability for gets a `403`, regardless of how
the request was made. See **Security → Authorization (RBAC)** for the
enforcement details.
