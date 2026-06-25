# Compliance Overview

A leadership-level summary of how spane's implemented security controls line up
with **ISO/IEC 27001:2022 Annex A** (primary framework) and **SOX IT general
controls** (secondary). This is the scannable rollup; the per-control detail,
with evidence pointers for an auditor, is in the
[Compliance Control Mapping](compliance-mapping.md).

Every status below reflects what the code actually does today — controls that are
not fully satisfied are shown as **Partial** or **Roadmap**, not inflated.

## Control domains at a glance

| Domain | Status | Summary |
|--------|--------|---------|
| Access control & authorization | **Strong** | Capability-based RBAC, deny-by-default enforcement, anti-escalation guardrail (can't grant what you don't hold). |
| Authentication | **Partial** | JWT + SSO + forced first-login change + **TOTP MFA** (local accounts); **no token revocation/blacklist**. |
| Privileged access | **Partial** | Immutable superadmin, last-admin lockout guard, **required MFA for privileged accounts**; no just-in-time elevation. |
| Cryptography & secrets | **Strong** | OpenBao for all secrets with a fail-closed least-privilege AppRole; TLS 1.3; per-agent PKI. |
| Transport & config hardening | **Partial** | TLS 1.3, HSTS, secure cookies, `nosniff`; **no Content-Security-Policy / some headers unset**; **SSH-to-device host keys not validated** (accepted risk). |
| Logging & audit | **Strong** | Indexed `AuditLog`, sensitive-value redaction, configurable retention + purge. |
| Monitoring | **Partial** | Report-based surfacing of platform access events; **no real-time alerting/SIEM** on the audit trail. |
| Secure coding | **Strong** | SSRF, XXE, SSTI, CSV-injection, and arg-injection guards, each test- or code-verified. |
| Secure development lifecycle | **Partial** | CI gates: test suite + CWE-209 guard + `pip-audit` (blocking) + `bandit` (blocking) + CodeQL; **no documented threat-modeling**. |
| Vulnerability management | **Partial** | Dependabot (npm + backend `pip` + Actions), `pip-audit` (blocking) + `bandit` (blocking) + CodeQL SAST; **no container-image or secret scanning**. |

**Strong** = the control is implemented and verifiable. **Partial** = the core is
in place but a named element is missing.

## Known gaps / roadmap

These are the honest top-level shortfalls. Each maps to a Partial control in the
[detailed mapping](compliance-mapping.md), so the list doubles as a
framework-anchored hardening backlog:

1. **JWT refresh-token revocation/blacklist** — none; refresh tokens stay valid for 7 days (A.8.5). *(TOTP MFA is now implemented — see [Multi-Factor Authentication](mfa.md).)*
2. **SSH device host-key validation** — not performed; accepted risk (first-connection MITM on the trusted management network) (A.5.14).
3. **Content-Security-Policy and some Django security headers** (`CSRF_COOKIE_HTTPONLY`, explicit `X_FRAME_OPTIONS`) — not set; nginx supplies `X-Frame-Options` + HSTS (A.8.9).
4. **Container-image and secret scanning** (trivy / gitleaks) — not in CI; ingest-service deps are Dependabot-tracked but not `pip-audit`-blocked (A.8.8 / A.8.25).
5. **Periodic access recertification / review workflow** — not implemented (A.5.18).
6. **Real-time monitoring/alerting on the platform's own audit trail** — report-based only (A.8.16).
7. **Privileged-session recording / just-in-time elevation** — not implemented (A.8.2).

## SOX-aligned ITGCs

spane's controls align with the three IT general control pillars — access
controls / segregation of duties (the capability model + anti-escalation),
change management (PR + CI gates for code; templated, audit-logged config push
for devices), and audit trail / logging (`AuditLog`). Whether spane is *in SOX
scope* is a financial-relevance determination for the organization's auditors, so
this is framed as **SOX-aligned ITGCs**, not "SOX compliant." See the
[detailed mapping](compliance-mapping.md#sox-itgc-secondary).

## Standalone export

A combined, hand-over version of this overview and the detailed mapping is
available as a single document:
[spane-compliance-mapping.docx](spane-compliance-mapping.docx).
