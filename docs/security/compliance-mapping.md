# Compliance Control Mapping

This is the detailed working artifact mapping spane's **implemented** security
controls to **ISO/IEC 27001:2022 Annex A** (primary) and **SOX ITGC** (secondary).
Audience: the security team and an external auditor.

It builds on the [Security](overview.md) section — each row cites the relevant
detail page and the code that implements the control, rather than re-explaining
it here.

Scope the frameworks an environment is held to with
[`APPLICABLE_COMPLIANCE_FRAMEWORKS`](../compliance/applicable-frameworks.md);
frameworks outside that scope are excluded from the live `/compliance` surfaces
and evidence exports so they never read as failing.

!!! warning "Honesty rule"
    Every row is **Met / Partial / Gap** based on what the code actually does,
    verified against the source — not aspiration. A control spane does not fully
    satisfy is marked **Partial** or **Gap**, with the specific shortfall named in
    the row. The platform-level gaps already documented in this section (no JWT
    refresh-token revocation, no Content-Security-Policy, etc.) appear here
    against the controls they affect.

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
| **A.5.14** Information transfer | **Partial** | TLS 1.3 at the edge; mutual TLS for agent ingestion; secure/`SameSite` cookies; credentials only ever move via OpenBao. **Accepted-risk gap:** SSH to managed devices does not validate host keys (paramiko `AutoAddPolicy`) — a first-connection MITM exposure, accepted because host keys aren't pre-provisioned across a multi-vendor fleet and the management plane is trusted/segmented with per-device OpenBao creds (see [Supply Chain](supply-chain.md) for the full rationale). | `services/frontend/nginx.conf:27` (`ssl_protocols TLSv1.3`); `config/settings/production.py` cookie flags; SSH host-key risk in `apps/compliance/collector.py` (CodeQL `py/paramiko-missing-host-key-validation`); [Transport & Hardening](transport-and-hardening.md); agent mTLS in [Agent → Security](../agents/security.md). |
| **A.5.15** Access control | **Met** | Capability-based RBAC; deny-by-default permission class; 5 system roles + custom roles. | `apps/core/capabilities.py` (`ALL_CAPABILITIES`, 54 caps); `apps/core/permissions.py:135` (`DenyByDefault`); `tests/test_rbac_capabilities.py`; [Authorization](authorization.md). |
| **A.5.18** Access rights (provisioning / review / revocation) | **Partial** | Roles assigned/changed via API + UI with anti-escalation; revocation by role change/deactivate; SSO new users default to `viewer`. **Gap:** no periodic access recertification/review workflow. | `apps/core/rbac_views.py`; `apps/core/views.py` (`UserViewSet.assign_rbac_role`); UI `/settings/access-roles`; [Admin → Access Roles](../admin/access-roles.md). |
| **A.8.2** Privileged access rights | **Partial** | Privileged actions gated by `rbac:manage`; `superadmin` role immutable + non-deletable; last-admin lockout guard; anti-escalation; **MFA is required for privileged local accounts** (`MFA_REQUIRED_FOR_CAPABILITIES`, forced enrollment — see [Multi-Factor Authentication](mfa.md)). **Gap:** no just-in-time elevation or privileged-session recording. | `apps/core/models.py` (`RBACRole.save/delete` guards); `apps/core/rbac_views.py`; `apps/core/views.py` (last-admin guards); `apps/core/mfa.py`. |
| **A.8.3** Information access restriction | **Met** | Every endpoint declares a required capability (`HasCapability`); `DenyByDefault` fails closed; secret fields are write-only and never returned. | `apps/core/permissions.py:112` (`HasCapability`); `apps/credentials/serializers.py` (`write_only`); [Authorization](authorization.md), [Secrets](secrets.md). |
| **A.8.5** Secure authentication | **Partial** | JWT (SimpleJWT, HS256); Django password validators + complexity rules + forced first-login change; SSO (Google/Azure/Okta/GitHub) minting the same JWT; **TOTP MFA for local accounts** (RFC 6238; required for privileged accounts — see [Multi-Factor Authentication](mfa.md)). **Gap:** refresh tokens are not rotated and there is no blacklist/revocation (`ROTATE_REFRESH_TOKENS=False`, 7-day refresh validity). | `config/settings/base.py:348-350` (`SIMPLE_JWT`), `AUTH_PASSWORD_VALIDATORS`; `apps/sso/`; `apps/core/mfa.py`; [Authentication](authentication.md). |
| **A.8.8** Management of technical vulnerabilities | **Partial** | Dependabot (weekly) covers npm, backend `pip` (api + each ingest/stream service), and GitHub Actions; `pip-audit` blocks known-CVE backend dependencies in CI; `bandit` (medium+) blocks new SAST findings; CodeQL static analysis runs via GitHub default setup (python/JavaScript-TypeScript/go/actions). **Gap:** `pip-audit` blocks `services/api` only (ingest-service deps are Dependabot-tracked but not CI-blocked); no container-image scanning (trivy/docker scout) or secret scanning (gitleaks). | `.github/dependabot.yml`; `.github/workflows/security-checks.yml` (`python-security-scan`); CodeQL default setup; [Supply Chain](supply-chain.md). |
| **A.8.9** Configuration management | **Partial** | Production settings module hardening (HSTS, SSL redirect, secure cookies, `nosniff`, proxy-SSL header); device config templating + drift detection; `ALLOW_CONFIG_PUSH` defaults false. **Gap:** no Content-Security-Policy; `CSRF_COOKIE_HTTPONLY` and an explicit Django `X_FRAME_OPTIONS` are not set (nginx sets `X-Frame-Options`/HSTS). | `config/settings/production.py`; `apps/config_templates/`; [Transport & Hardening](transport-and-hardening.md). |
| **A.8.15** Logging | **Met** | `AuditLog` (~46 event types, indexed); `log_event` captures actor/IP/user-agent/target; sensitive metadata keys redacted; configurable retention + scheduled purge. | `apps/core/models.py:256` (`AuditLog`); `apps/core/audit.py` (`log_event`, `scrub_sensitive`); `tests/test_audit.py`; [Audit Logging](audit-logging.md). |
| **A.8.16** Monitoring | **Partial** | Daily-ops report surfaces platform access events (failed/after-hours logins, new source IPs, admin/config actions); device-side auth-anomaly detection (brute-force/off-hours). **Gap:** no real-time alerting/SIEM on the platform's own audit trail. | `apps/reports/daily_ops.py` ("spane Access Events"); [Audit Logging](audit-logging.md). |
| **A.8.24** Use of cryptography / key management | **Met** | All secrets in OpenBao (hvac KV v2); TLS 1.3 external; per-agent EC P-384 PKI certificates; JWT HS256. Note: key rotation is OpenBao-supported but operationally manual (no automated rotation policy). | `apps/credentials/vault.py`; `services/frontend/nginx.conf`; `apps/agents/pki.py`; [Secrets](secrets.md). |
| **A.8.25** Secure development lifecycle | **Partial** | CI gates that must pass before merge: full pytest suite (`api-tests.yml`), CWE-209 exception-exposure guard (CI + pre-commit + test), `pip-audit` (blocking) + `bandit` (blocking, medium+), and CodeQL; capability drift-guard test. **Gap:** no documented threat-modeling/security-design process; no container-image/secret-scanning gate. | `.github/workflows/api-tests.yml`, `security-checks.yml`; `scripts/check_exception_exposure.py`; `tests/test_rbac_capabilities.py`; [Supply Chain](supply-chain.md). |
| **A.8.28** Secure coding | **Met** | SSRF guard (`validate_outbound_url`, blocks cloud-metadata); `defusedxml` (XXE); Jinja2 `SandboxedEnvironment` (SSTI); CIDR validation (nmap arg-injection); `csv_safe` (CSV formula injection); parameterized subprocess (no shell). | `apps/core/net_safety.py:64`; `apps/devices/management/commands/run_discovery.py`; `apps/compliance/engine.py`, `apps/config_templates/render.py`; `apps/devices/serializers.py`; `apps/core/audit.py` (`csv_safe`); [Input Hardening](input-hardening.md). |

### Totals

- **Met: 5** — A.5.15, A.8.3, A.8.15, A.8.24, A.8.28
- **Partial: 8** — A.5.14, A.5.18, A.8.2, A.8.5, A.8.8, A.8.9, A.8.16, A.8.25
- **Gap: 0** — see the honesty note below.

### Honesty note on the 0-Gap result

No Annex A control in this mapping is a full **Gap** because each one listed has
genuine, verifiable implementation — they were selected *because* spane
implements them at least partially. That is not the same as "no gaps." The
honest, granular shortfalls are the **measures that are entirely absent**, which
sit *inside* the Partial controls and are listed plainly here so a reviewer can
see them without reading every row:

- **JWT refresh-token revocation/blacklist** — none; a refresh token is valid for
  its full 7-day life (A.8.5). (TOTP MFA *is* now implemented — see
  [Multi-Factor Authentication](mfa.md).)
- **SSH device host-key validation** — not performed; an explicit accepted risk
  (A.5.14).
- **Real-time monitoring/alerting on the platform's own audit trail** — only
  batch/daily reporting exists (A.8.16).
- **Periodic access recertification/review** — no workflow (A.5.18).
- **Content-Security-Policy** (and `CSRF_COOKIE_HTTPONLY`) — not set (A.8.9).
- **Privileged-session recording / just-in-time elevation** — not implemented
  (A.8.2).

A control was downgraded during review to keep this honest: **A.5.14 moved from
Met to Partial** once the SSH host-key accepted risk was weighed against an
otherwise-strong transport story. Scope note: this mapping covers only the Annex
A controls spane's platform actually touches; whole domains it does not implement
(e.g. physical security A.7, HR security A.6, supplier relationships A.5.19–A.5.22)
are out of scope and are not counted as Met, Partial, or Gap.

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
- **Note:** MFA is required for privileged local accounts (see ISO A.8.2 / A.8.5
  and [Multi-Factor Authentication](mfa.md)).

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
