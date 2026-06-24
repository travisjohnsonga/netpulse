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
| Authentication | **Partial** | JWT + SSO + forced first-login change; **no MFA, no token revocation/blacklist**. |
| Privileged access | **Partial** | Immutable superadmin, last-admin lockout guard; **no MFA / just-in-time elevation**. |
| Cryptography & secrets | **Strong** | OpenBao for all secrets with a fail-closed least-privilege AppRole; TLS 1.3; per-agent PKI. |
| Transport & config hardening | **Partial** | TLS 1.3, HSTS, secure cookies, `nosniff`; **no Content-Security-Policy / some headers unset**. |
| Logging & audit | **Strong** | Indexed `AuditLog`, sensitive-value redaction, configurable retention + purge. |
| Monitoring | **Partial** | Report-based surfacing of platform access events; **no real-time alerting/SIEM** on the audit trail. |
| Secure coding | **Strong** | SSRF, XXE, SSTI, CSV-injection, and arg-injection guards, each test- or code-verified. |
| Secure development lifecycle | **Partial** | CI test gate + CWE-209 guard + CodeQL; **backend dependency/SAST scanning gate pending**. |
| Vulnerability management | **Roadmap** | CodeQL (default setup) + Dependabot; **npm-only today — backend `pip` scanning in flight (PR #34)**. |

**Strong** = the control is implemented and verifiable. **Partial** = the core is
in place but a named element is missing. **Roadmap** = mostly pending, with
remediation identified.

## Known gaps / roadmap

These are the honest top-level shortfalls. Each maps to a Partial control in the
[detailed mapping](compliance-mapping.md), so the list doubles as a
framework-anchored hardening backlog:

1. **Multi-factor authentication** — not implemented (auth + privileged access; ISO A.8.5 / A.8.2).
2. **JWT refresh-token revocation/blacklist** — none; refresh tokens stay valid for 7 days (A.8.5).
3. **Content-Security-Policy and some Django security headers** (`CSRF_COOKIE_HTTPONLY`, explicit `X_FRAME_OPTIONS`) — not set; nginx supplies `X-Frame-Options` + HSTS (A.8.9).
4. **Backend dependency CVE scanning + pip/Actions Dependabot** — npm-only today; `pip-audit` and broadened Dependabot are in review (PR #34) (A.8.8 / A.8.25).
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
