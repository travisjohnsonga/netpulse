# Compliance Control Mapping

This is the detailed working artifact mapping spane's **implemented** security
controls to **ISO/IEC 27001:2022 Annex A** (primary) and **SOX ITGC** (secondary).
Audience: the security team and an external auditor.

It builds on the [Security](overview.md) section — each row cites the relevant
detail page and the code that implements the control, rather than re-explaining
it here.

!!! warning "Honesty rule"
    Every row is **Met / Partial / Gap** based on what the code actually does,
    verified against the source — not aspiration. A control spane does not fully
    satisfy is marked **Partial** or **Gap**, with the specific shortfall named in
    the row. The platform-level gaps already documented in this section (no MFA,
    no JWT refresh-token revocation, no Content-Security-Policy, npm-only
    Dependabot, etc.) appear here against the controls they affect.

**Scope.** This maps the spane *platform's own* security posture (its ISMS
controls), not spane's product features that help customers meet controls.

## Status legend

| Status | Meaning |
|--------|---------|
| **Met** | Implemented in code and verifiable today. |
| **Partial** | Core of the control is implemented, but a named element is missing. |
| **Gap** | Control is relevant but not implemented. |

## ISO/IEC 27001:2022 Annex A

| Control | Status | How spane addresses it | Evidence (where an auditor looks) |
|---------|--------|------------------------|-----------------------------------|
| **A.5.14** Information transfer | **Met** | TLS 1.3 at the edge; mutual TLS for agent ingestion; secure/`SameSite` cookies; credentials only ever move via OpenBao. | `services/frontend/nginx.conf:27` (`ssl_protocols TLSv1.3`); `config/settings/production.py` cookie flags; [Transport & Hardening](transport-and-hardening.md); agent mTLS in [Agent → Security](../agents/security.md). |
| **A.5.15** Access control | **Met** | Capability-based RBAC; deny-by-default permission class; 5 system roles + custom roles. | `apps/core/capabilities.py` (`ALL_CAPABILITIES`, 54 caps); `apps/core/permissions.py:135` (`DenyByDefault`); `tests/test_rbac_capabilities.py`; [Authorization](authorization.md). |
| **A.5.18** Access rights (provisioning / review / revocation) | **Partial** | Roles assigned/changed via API + UI with anti-escalation; revocation by role change/deactivate; SSO new users default to `viewer`. **Gap:** no periodic access recertification/review workflow. | `apps/core/rbac_views.py`; `apps/core/views.py` (`UserViewSet.assign_rbac_role`); UI `/settings/access-roles`; [Admin → Access Roles](../admin/access-roles.md). |
| **A.8.2** Privileged access rights | **Partial** | Privileged actions gated by `rbac:manage`; `superadmin` role immutable + non-deletable; last-admin lockout guard; anti-escalation. **Gap:** no MFA on privileged accounts, no just-in-time elevation or privileged-session recording (see A.8.5). | `apps/core/models.py` (`RBACRole.save/delete` guards); `apps/core/rbac_views.py`; `apps/core/views.py` (last-admin guards). |
| **A.8.3** Information access restriction | **Met** | Every endpoint declares a required capability (`HasCapability`); `DenyByDefault` fails closed; secret fields are write-only and never returned. | `apps/core/permissions.py:112` (`HasCapability`); `apps/credentials/serializers.py` (`write_only`); [Authorization](authorization.md), [Secrets](secrets.md). |
| **A.8.5** Secure authentication | **Partial** | JWT (SimpleJWT, HS256); Django password validators + complexity rules + forced first-login change; SSO (Google/Azure/Okta/GitHub) minting the same JWT. **Gap:** no MFA; refresh tokens are not rotated and there is no blacklist/revocation (`ROTATE_REFRESH_TOKENS=False`, 7-day refresh validity). | `config/settings/base.py:348-350` (`SIMPLE_JWT`), `AUTH_PASSWORD_VALIDATORS`; `apps/sso/`; [Authentication](authentication.md). |
| **A.8.8** Management of technical vulnerabilities | **Partial** | CodeQL static analysis runs via GitHub default setup (python/JavaScript-TypeScript/go/actions); Dependabot weekly. **Gap:** Dependabot covers npm only (no backend `pip`, no GitHub Actions); no `pip-audit`/`bandit` in CI on `main`. Remediation in review (PR #34). | `.github/dependabot.yml`; CodeQL default setup (repo Settings → Code security); [Supply Chain](supply-chain.md). |
| **A.8.9** Configuration management | **Partial** | Production settings module hardening (HSTS, SSL redirect, secure cookies, `nosniff`, proxy-SSL header); device config templating + drift detection; `ALLOW_CONFIG_PUSH` defaults false. **Gap:** no Content-Security-Policy; `CSRF_COOKIE_HTTPONLY` and an explicit Django `X_FRAME_OPTIONS` are not set (nginx sets `X-Frame-Options`/HSTS). | `config/settings/production.py`; `apps/config_templates/`; [Transport & Hardening](transport-and-hardening.md). |
| **A.8.15** Logging | **Met** | `AuditLog` (~46 event types, indexed); `log_event` captures actor/IP/user-agent/target; sensitive metadata keys redacted; configurable retention + scheduled purge. | `apps/core/models.py:256` (`AuditLog`); `apps/core/audit.py` (`log_event`, `scrub_sensitive`); `tests/test_audit.py`; [Audit Logging](audit-logging.md). |
| **A.8.16** Monitoring | **Partial** | Daily-ops report surfaces platform access events (failed/after-hours logins, new source IPs, admin/config actions); device-side auth-anomaly detection (brute-force/off-hours). **Gap:** no real-time alerting/SIEM on the platform's own audit trail. | `apps/reports/daily_ops.py` ("spane Access Events"); [Audit Logging](audit-logging.md). |
| **A.8.24** Use of cryptography / key management | **Met** | All secrets in OpenBao (hvac KV v2); TLS 1.3 external; per-agent EC P-384 PKI certificates; JWT HS256. Note: key rotation is OpenBao-supported but operationally manual (no automated rotation policy). | `apps/credentials/vault.py`; `services/frontend/nginx.conf`; `apps/agents/pki.py`; [Secrets](secrets.md). |
| **A.8.25** Secure development lifecycle | **Partial** | CI gates: full pytest suite (`api-tests.yml`), CWE-209 exception-exposure guard (CI + pre-commit + test), CodeQL; capability drift-guard test. **Gap:** no backend dependency/SAST scanning gate in CI on `main` (PR #34); no documented threat-modeling process. | `.github/workflows/api-tests.yml`, `security-checks.yml`; `scripts/check_exception_exposure.py`; `tests/test_rbac_capabilities.py`; [Supply Chain](supply-chain.md). |
| **A.8.28** Secure coding | **Met** | SSRF guard (`validate_outbound_url`, blocks cloud-metadata); `defusedxml` (XXE); Jinja2 `SandboxedEnvironment` (SSTI); CIDR validation (nmap arg-injection); `csv_safe` (CSV formula injection); parameterized subprocess (no shell). | `apps/core/net_safety.py:64`; `apps/devices/management/commands/run_discovery.py`; `apps/compliance/engine.py`, `apps/config_templates/render.py`; `apps/devices/serializers.py`; `apps/core/audit.py` (`csv_safe`); [Input Hardening](input-hardening.md). |

### Totals

- **Met: 6** — A.5.14, A.5.15, A.8.3, A.8.15, A.8.24, A.8.28
- **Partial: 7** — A.5.18, A.8.2, A.8.5, A.8.8, A.8.9, A.8.16, A.8.25
- **Gap: 0** (every relevant control is at least partially implemented; the named
  shortfalls in the Partial rows are the framework-mapped hardening roadmap).

## SOX ITGC (secondary)

!!! note "Scope framing"
    Whether spane falls in **SOX scope** depends on its relevance to financial
    reporting (e.g. whether it monitors systems that support financial
    statements) — that is a determination for the organization's auditors, not a
    property of the software. This section therefore describes **SOX-aligned
    IT general controls**, not "SOX compliance."

spane's controls map to the three ITGC pillars:

### (a) Access controls / segregation of duties

The capability model **is** the SoD control. Least-privilege roles
(`apps/core/capabilities.py`), `DenyByDefault` enforcement, and the
**anti-escalation guardrail** — you cannot grant a capability you do not yourself
hold (`apps/core/rbac_views.py` `_check_escalation`; enforced again on user role
assignment in `apps/core/views.py`) — together prevent a single actor from
self-granting incompatible privileges. The last-admin guard prevents removing the
final administrator. Role/permission changes are audit-logged.

- **Evidence:** `apps/core/capabilities.py`, `apps/core/permissions.py`,
  `apps/core/rbac_views.py` (`_check_escalation`), `tests/test_rbac_capabilities.py`,
  AuditLog `user_role_changed` events.
- **Caveat:** no MFA on privileged accounts (see ISO A.8.2 / A.8.5).

### (b) Change management

Two change surfaces, both controlled:

- **Platform code changes** — GitHub pull-request flow plus CI gates that must
  pass before merge: the pytest suite (`api-tests.yml`), the CWE-209
  exception-exposure guard (`security-checks.yml`), and the RBAC capability
  drift-guard test (`tests/test_rbac_capabilities.py`).
- **Managed-device config changes** — config-push templates with
  `ALLOW_CONFIG_PUSH` defaulting to false (read-only by default); every push
  attempt is audit-logged (`config_pushed`); config versioning + running/startup
  drift detection + the compliance engine track change.
- **Evidence:** `.github/workflows/`, `apps/config_templates/`,
  `apps/compliance/`, AuditLog `config_pushed` events.

### (c) Audit trail / logging

`AuditLog` records security-relevant events (logins, user/role changes, config
pushes, credential access, integration syncs, agent enroll/revoke) with actor,
IP, target, and timestamp; sensitive values are redacted; retention is
configurable with a scheduled purge.

- **Evidence:** `apps/core/models.py` (`AuditLog`), `apps/core/audit.py`,
  `tests/test_audit.py`, [Audit Logging](audit-logging.md).

## How to use this document

For an auditor: each Evidence cell points at the artifact that proves the
control — a source file, a test, a settings value, or a UI/API location. For the
leadership summary, see [Compliance Overview](compliance-overview.md).
