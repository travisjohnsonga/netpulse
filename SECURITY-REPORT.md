# NetPulse — Pre-Production Security Audit

Scope: `services/` (Django API + ingest/engine services + React frontend).
Method: automated scanning (pip-audit, bandit, npm audit, secret grep, git
history) + the manual checklist from CLAUDE.md. Performed against the current
`main` branch.

**Verdict: no CRITICAL findings. Resolve the HIGH findings (auth rate-limiting,
paramiko CVE) before production; the MEDIUM items should follow shortly after.**

---

## Summary

| Severity | Count | Must fix before prod? |
|---|---|---|
| CRITICAL | 0 | — |
| HIGH | 2 | Yes |
| MEDIUM | 2 | Recommended |
| LOW / INFO | 6 | First patch / accept |

---

## CRITICAL
None found.

## HIGH

### H1 — No rate limiting on authentication endpoints
`/api/auth/token/` (and the API generally) has **no DRF throttling configured**
(`DEFAULT_THROTTLE_CLASSES`/`_RATES` absent in `config/settings/base.py`). This
allows unthrottled credential brute-force / token-refresh abuse.
**Fix:** add `AnonRateThrottle` + a scoped throttle on the token-obtain view
(e.g. `5/min` per IP), and a global authenticated rate. Front with the reverse
proxy as defence-in-depth.

### H2 — Vulnerable transitive dependency: paramiko 4.0.0
`pip-audit` flags **paramiko 4.0.0 — CVE-2026-44405** (pulled in transitively by
netmiko / ncclient for device SSH). No fixed version is published yet at audit
time.
**Fix:** pin/upgrade paramiko once a fixed release lands; track the advisory.
(`pip` itself was also flagged — CVE-2026-3219 / -6357 — but pip is a build-time
tool, not shipped in the runtime image; upgrade the builder image's pip.)

## MEDIUM

### M1 — SSRF surface in outbound `urllib.urlopen` (bandit B310)
`apps/integrations/netbox.py` and `apps/core/views.py` open admin/operator-
supplied or internal URLs with `urllib.request.urlopen`. The NetBox importer URL
is admin-provided and RBAC-gated, but no scheme/host allowlist is enforced.
**Fix:** validate the scheme (http/https only) and consider a host allowlist for
the NetBox importer.

### M2 — Jinja2 `autoescape=False` (bandit B701, High by bandit, Medium in context)
`apps/telemetry/config_gen.py` renders templates with `autoescape=False`. These
render **device CLI config** (not HTML served to browsers), so XSS does not
apply; output is additionally ASCII-sanitised before push. Risk is low in
context, but the flag warrants an explicit decision.
**Fix:** keep `autoescape=False` (correct for CLI config) but document with a
`# nosec B701` and a comment, or switch to `select_autoescape([])`.

## LOW / INFO

- **L1 — InfluxDB Flux built via f-strings** (`apps/devices/metrics_influx.py`):
  `device_id` (integer PK) and `period` (validated against `VALID_PERIODS`) are
  interpolated into Flux query strings. Currently safe given the inputs;
  recommend keeping the `int()`/allowlist guards as defence-in-depth.
- **L2 — npm esbuild/vite (2 moderate)**: dev-server-only advisory
  (GHSA-67mh-4wv8-2f99); not present in the production static build. Upgrade Vite
  in a maintenance window (breaking).
- **L3 — pip build-tool CVEs**: not shipped at runtime (build stage only).
- **I1 — bandit B105 ×8 (hardcoded_password_string)**: false positives — they are
  `TextChoices` labels (`PASSWORD = "password"`) and placeholder defaults
  (`"<AUTH_KEY>"`, `"<PRIV_KEY>"`), not secrets.
- **I2 — bandit B110/B112 ×7 (try/except/pass|continue)**: defensive
  cleanup/best-effort blocks (connection close, optional parsing). Acceptable.

---

## Manual checklist (CLAUDE.md)

| Check | Result | Notes |
|---|---|---|
| All API endpoints require authentication | ✅ PASS | Global `NetPulsePermission` default; only `/api/health/*` and JWT token endpoints are `AllowAny` by design |
| No SQL injection (ORM used correctly) | ✅ PASS | Django ORM throughout; OpenSearch queries built as DSL dicts; InfluxDB Flux guarded (see L1) |
| No command injection in subprocess calls | ✅ PASS | No `shell=True`/`os.system`/`subprocess`; device comms via Netmiko/ncclient/sockets/icmplib (raw socket, no shell) |
| No hardcoded secrets anywhere | ✅ PASS | Secret grep clean (matches are field names/mount points) |
| No secrets in logs | ✅ PASS | Credentials scrubbed; `test_snmp_publish` asserts no secrets in NATS payloads |
| OpenBao used for all credentials | ✅ PASS | `vault_path` references in PostgreSQL; secret values never stored in DB |
| JWT tokens have appropriate expiry | ✅ PASS | access 1h, refresh 7d (`SIMPLE_JWT`) |
| Rate limiting on auth endpoints | ❌ FAIL | See H1 |
| CORS configured correctly | ✅ PASS | Production uses `CORS_ALLOWED_ORIGINS` env allowlist; dev allows all |
| Containers run as non-root | ✅ PASS | `USER netpulse` in all 8 service Dockerfiles |
| All dependencies have permissive licenses | ✅ PASS | MIT/BSD/Apache-2.0/MPL-2.0 (PostgreSQL, OpenSearch, Valkey, NATS, OpenBao); no copyleft-on-use |

---

## Automated scan outputs (condensed)

- `pip-audit`: 3 vulns in 2 packages — paramiko 4.0.0 (CVE-2026-44405), pip 26.0.1 (build tool).
- `bandit -ll`: 1 High (B701 jinja, justified), 2 Medium (B310 urllib), 15 Low (8×B105 FP, 7×try/except).
- `npm audit --audit-level=moderate`: 2 moderate (esbuild/vite, dev-only).
- Secret grep + `git log -p --all` secret scan: no committed secret literals.

## Recommendation
Ship-blockers: **H1** (add auth throttling). **H2** should be tracked and patched
as soon as a paramiko fix is available. Address M1/M2 in the first hardening pass.
No CRITICAL issues block production once H1 is resolved.

*Generated by an automated + manual pre-production review against `main`.*
