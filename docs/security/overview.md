# Security Overview

This section documents the security controls that are **actually implemented**
in spane today, with a pointer to the code that enforces each one. It is written
for engineers and operators running the platform, and for reviewers checking the
claims against the source. Where a control is configuration-gated (for example,
the production HTTPS hardening), that is called out explicitly.

> Every statement here is sourced to a file in the repository. If a control
> isn't described in this section, don't assume it exists — check the code.

## Design philosophy

spane is built secret-first and deny-by-default. Credentials live only in
OpenBao and are referenced from PostgreSQL by path, never stored in plaintext or
returned by the API. Authorization fails closed: an endpoint that declares no
capability is denied, not granted. External-facing transport is TLS-only in
production, and the few places that take a URL or a template from a user are run
through an SSRF guard or a sandbox. These are the load-bearing controls; the
rest of this section documents each in detail.

## Control summary

| Area | What's enforced | Where |
|------|-----------------|-------|
| [Authentication](authentication.md) | JWT (1h access / 7d refresh, HS256), password validators, forced first-login change, SSO that mints the same JWT | `config/settings/base.py`, `apps/core/serializers.py`, `apps/sso/` |
| [Authorization (RBAC)](authorization.md) | Deny-by-default permission class; 54-capability catalog; system + custom roles; per-endpoint `HasCapability`; anti-escalation; superadmin immutability; drift-guard test | `apps/core/capabilities.py`, `apps/core/permissions.py`, `apps/core/rbac_views.py` |
| [Secrets management](secrets.md) | OpenBao (hvac) KV storage; least-privilege read-only AppRole; fail-closed broker; write-only secret serializers | `apps/credentials/vault.py`, `apps/collectors/secret_broker.py` |
| [Transport & hardening](transport-and-hardening.md) | HSTS (1yr, preload, subdomains), SSL redirect, secure/HttpOnly/SameSite cookies, CSRF trusted origins, nosniff, proxy SSL header, TLS 1.3-only nginx | `config/settings/production.py`, `services/frontend/nginx.conf` |
| [Input hardening](input-hardening.md) | SSRF guard on outbound URLs (blocks cloud-metadata); `defusedxml` for nmap XML; Jinja2 sandbox for templates; CIDR validation; CSV formula-injection guard | `apps/core/net_safety.py`, `apps/compliance/engine.py`, `apps/config_templates/render.py` |
| [Audit logging](audit-logging.md) | `AuditLog` with indexed event types; `log_event` captures actor/IP/target; sensitive keys redacted; configurable retention + purge | `apps/core/models.py`, `apps/core/audit.py` |
| [Supply chain](supply-chain.md) | CI test gate (pytest), CWE-209 exception-exposure guard (CI + pre-commit), Dependabot (npm) | `.github/workflows/`, `scripts/check_exception_exposure.py` |

## Agent security

The spane monitoring agent has its own transport and identity model (mutual TLS
with a per-agent OpenBao-PKI certificate, outbound-only, enrollment-token gated).
That is documented in detail under **Agent → [Security](../agents/security.md)**
and is not repeated here.
