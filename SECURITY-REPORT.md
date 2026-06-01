# NetPulse — Pre-Production Security Audit

Scope: `services/` (Django API + ingest/engine services + React frontend).
Method: automated scanning (pip-audit, bandit, npm audit, gitleaks secret scan
over the working tree **and** 211 commits of git history) plus the manual
checklist from CLAUDE.md. Performed against the current `main` branch on
2026-06-01.

**Verdict: no CRITICAL findings. The only outstanding HIGH is an upstream
dependency CVE with no published fix (H2 — paramiko); track and patch when a
release lands. H1 (auth rate limiting) is resolved and now additionally
hardened to throttle per client IP behind the reverse proxy. MEDIUM items
are recommended hardening, not ship-blockers.**

---

## Summary

| Severity | Count | Must fix before prod? |
|---|---|---|
| CRITICAL | 0 | — |
| HIGH | 2 (H1 ✅ resolved + hardened; H2 no upstream fix) | H2 track |
| MEDIUM | 2 | Recommended |
| LOW / INFO | 6 | First patch / accept |

Re-scan delta vs the previous report: no new findings. The code added since the
last audit (admin user-management API, default/system alert rules, proxy-aware
auth throttling) introduced **no** new bandit Medium/High issues and no new
dependencies; total scanned grew to 11,452 LoC.

---

## CRITICAL
None found.

## HIGH

### H1 — Auth endpoint rate limiting — ✅ RESOLVED + HARDENED
`/api/auth/token/` and `/api/auth/token/refresh/` use a DRF `ScopedRateThrottle`
("auth" scope, default `10/min`, configurable via `AUTH_THROTTLE_RATE`), backed
by the Valkey cache. The rest of the API is intentionally unthrottled to avoid
limiting health checks / normal traffic.

**Hardening this cycle:** the throttle worked when hitting the API directly but
not through the frontend nginx (the production path) — DRF keyed the bucket on
`REMOTE_ADDR`, which is the nginx container IP for **every** client, collapsing
the per-IP limit into a single shared global bucket (one attacker could lock out
all users; per-attacker limiting did not actually apply). Fixed by setting
`REST_FRAMEWORK["NUM_PROXIES"]=1` (env-overridable) so DRF reads the real client
IP from `X-Forwarded-For`, and adding `proxy_set_header X-Forwarded-For` on the
nginx `/api/` location. Verified end-to-end through nginx (401 ×10 → 429) and by
two tests: `test_token_endpoint_is_rate_limited` (429 after the limit) and
`test_throttle_is_per_client_ip_behind_proxy` (distinct client IPs get
independent buckets).

### H2 — Vulnerable transitive dependency: paramiko 4.0.0
`pip-audit -r requirements.txt` flags **paramiko 4.0.0 — CVE-2026-44405** (pulled
in transitively by netmiko / ncclient for device SSH). No fixed version is
published yet — pip-audit lists no fix version.
**Fix:** pin/upgrade paramiko once a fixed release lands; track the advisory.
(`pip` is no longer flagged here because the scan targets `requirements.txt`;
the build-tool CVE only affects the builder image, not the shipped runtime.)

## MEDIUM

### M1 — SSRF surface in outbound `urllib.urlopen` (bandit B310)
`apps/integrations/netbox.py:60` and `apps/core/views.py:109` open admin/operator-
supplied or internal URLs with `urllib.request.urlopen`. The NetBox importer URL
is admin-provided and RBAC-gated, but no scheme/host allowlist is enforced.
**Fix:** validate the scheme (http/https only) and consider a host allowlist for
the NetBox importer.

### M2 — Jinja2 `autoescape=False` (bandit B701)
`apps/telemetry/config_gen.py:63` renders templates with `autoescape=False`.
These render **device CLI config** (not HTML served to browsers), so XSS does
not apply; output is additionally ASCII-sanitised before push. Low risk in
context, but the flag warrants an explicit decision.
**Fix:** keep `autoescape=False` (correct for CLI config) but annotate with
`# nosec B701` and a comment, or switch to `select_autoescape([])`.

## LOW / INFO

- **L1 — InfluxDB Flux built via f-strings** (`apps/devices/metrics_influx.py`):
  `device_id` (int PK) and `period` (validated against `VALID_PERIODS`) are
  interpolated into Flux strings. Safe given the inputs; keep the `int()`/
  allowlist guards as defence-in-depth.
- **L2 — npm esbuild/vite (2 moderate)**: dev-server-only advisory
  (GHSA-67mh-4wv8-2f99); not present in the production static build. Upgrade Vite
  in a maintenance window (breaking — `vite@8`).
- **L3 — pip build-tool CVEs**: not shipped at runtime (build stage only).
- **I1 — bandit B105 ×8 (hardcoded_password_string)**: false positives —
  `TextChoices` labels (`PASSWORD = "password"`) and placeholder defaults
  (`YOUR-AUTH-KEY-HERE`), not secrets.
- **I2 — bandit B110/B112 ×7 (try/except/pass|continue)**: defensive best-effort
  blocks (connection close, optional parsing). Acceptable.
- **I3 — gitleaks doc false positives ×3**: `generic-api-key` matched plain
  documentation text ("NUM_PROXIES=1", "API, Juniper/Arista") in CLAUDE.md /
  ARCHITECTURE.md. Not secrets.

## Secret scan (gitleaks)

- **Working tree (`--no-git`)**: 4 hits — 3 doc false positives (I3) and the real
  OpenBao service token in `.env`. `.env` is **gitignored and untracked**
  (`git check-ignore .env` ✅, `git ls-files` shows it is not tracked), so the
  local runtime secrets file is expected to hold real values and is never
  committed.
- **Git history (211 commits)**: 3 hits, all the same documentation
  false positives. **No real secret literals are committed.**

---

## New-this-cycle code review (admin user management API)

The new `AdminOnly` `UserViewSet` (`/api/users/`) was reviewed specifically for
privilege-escalation and account-lockout risks:

| Check | Result |
|---|---|
| Endpoint gated to admins | ✅ `permission_classes=[AdminOnly]` (superuser or `role=admin`) |
| Cannot self-escalate to Django superuser | ✅ `is_superuser` is read-only in the serializer |
| Passwords never returned | ✅ `password` is write-only; validated with Django validators |
| Cannot lock out administration | ✅ guards block self-delete and deleting/demoting/deactivating the last active admin |
| `/users/me/*` not shadowed by the router | ✅ explicit paths ordered before the viewset; verified by URL resolution + test |

No issues found; 17 tests cover the guards.

---

## Manual checklist (CLAUDE.md)

| Check | Result | Notes |
|---|---|---|
| All API endpoints require authentication | ✅ PASS | Global `NetPulsePermission` default; only `/api/health/*` and JWT token endpoints are `AllowAny` by design |
| Role-based permissions enforced (not just is_authenticated) | ✅ PASS | `NetPulsePermission` (read/write by role) + `AdminOnly` on user mgmt / system config |
| No SQL injection (ORM used correctly) | ✅ PASS | Django ORM throughout; OpenSearch queries built as DSL dicts; InfluxDB Flux guarded (see L1) |
| No command injection in subprocess calls | ✅ PASS | No `shell=True`/`os.system`; device comms via Netmiko/ncclient/sockets/icmplib |
| No hardcoded secrets anywhere | ✅ PASS | gitleaks history clean; `.env` gitignored |
| No secrets in logs | ✅ PASS | Credentials scrubbed; `test_snmp_publish` asserts no secrets in NATS payloads |
| OpenBao used for all credentials | ✅ PASS | `vault_path` references in PostgreSQL; secret values never stored in DB |
| JWT tokens have appropriate expiry | ✅ PASS | access 1h, refresh 7d (`SIMPLE_JWT`) |
| Rate limiting on auth endpoints | ✅ PASS | H1 resolved + per-client behind proxy (`NUM_PROXIES`) |
| CORS configured correctly | ✅ PASS | Production uses `CORS_ALLOWED_ORIGINS` env allowlist; dev allows all |
| Containers run as non-root | ✅ PASS | `USER netpulse` in service Dockerfiles |
| All dependencies have permissive licenses | ✅ PASS | MIT/BSD/Apache-2.0/MPL-2.0; no copyleft-on-use |

---

## Automated scan outputs (condensed)

- `pip-audit -r requirements.txt`: **1 vuln** — paramiko 4.0.0 (CVE-2026-44405), no fix version.
- `bandit -ll -r apps`: **1 High** (B701 jinja, justified), **2 Medium** (B310 urllib), **15 Low** (8×B105 FP, 7×try/except). 11,452 LoC scanned.
- `npm audit --audit-level=moderate`: **2 moderate** (esbuild/vite, dev-only).
- `gitleaks` (tree + 211-commit history): no real committed secrets; `.env` gitignored; doc false positives only.

## Recommendation
No CRITICAL or net-new findings. **H1 is resolved and hardened.** The only
open HIGH is **H2 (paramiko CVE-2026-44405)**, which has no upstream fix yet —
track the advisory and bump as soon as a patched release ships. Address M1/M2 in
the first hardening pass. No issues block production at this time.

*Generated by an automated + manual pre-production review against `main`, 2026-06-01.*
