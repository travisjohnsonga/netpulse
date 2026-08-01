# spane — Security Posture Update (2026-08-01, app-v0.7.1)

Full-day remediation sweep shipped as the app-v0.7.1 patch release (PRs
#173–#182). Full api suite (2567) green; security-checks CI gate green.

## Dependency vulnerability posture

- **Fixed (npm, #173/#178):** js-yaml → 4.3.0 (CVE-2026-59869, the previous
  overrides pin had itself gone stale), brace-expansion → 5.0.9
  (CVE-2026-13149), dompurify → 3.4.12, postcss → 8.5.25 (build-only;
  GHSA-r28c-9q8g-f849, caught by npm-audit though absent from Dependabot),
  plus the npm minor-patch group (swagger-ui-react 5.32.11, vite 7.3.6, …).
- **Fixed (pip, #180):** aiosmtplib ~=3.0 → ~=5.1 (PYSEC-2026-2338). The
  functional test for that bump exposed a REAL latent bug: `SMTP.ehlo()` is
  keyword-only in 3.x AND 5.x, so every SMTP service check had been silently
  TypeError-ing into a "down" result — fixed and now regression-tested.
- **Eliminated rather than ignored (#180):** `python-jose` removed entirely —
  it was a dead requirement (zero imports in spane; social-core's OIDC/JWT
  work uses PyJWT, which SimpleJWT already provides; `pip show` Required-by
  empty). Removing it took the unfixable **ecdsa** advisory (PYSEC-2026-1325,
  no fixed release exists upstream) out of the dependency tree entirely — the
  pip-audit gate now runs with **zero ignore flags**.
- **Accepted-risk, documented, dismissed-with-reason on GitHub (#173):**
  react-router 6.x moderates (fix = the v7 major; migration tracked in
  `docs/roadmap.md`; the SSR-hydration CVE doesn't apply to this SPA at all)
  and immutable 3.8.3 highs (pulled only by swagger-ui-react whose `^3.x`
  constraint blocks the fix; the DoS "attacker input" is our own OpenAPI
  spec; revisit when upstream moves to immutable 4.x).
- **Bandit:** B104 audit found **zero real bind-to-all-interfaces issues**;
  the single flagged `"0.0.0.0"` is a data literal (synthetic-IP matching in a
  migration) carrying a targeted, justified `# nosec B104`. The CI gate
  (medium+, `--skip B507` with its documented acceptance rationale) is clean.

## Vulnerability class found & fixed: curl|bash stdin poisoning (#177)

`install.sh` (run via `curl … | bash`) executes `setup.sh`, whose interactive
`read` prompts inherited **stdin = the script pipe** — the prompts consumed the
installer's own remaining text as their "answers". Found live: `COLLECTOR_IP`
captured install.sh's `# ─── Done` banner line, and setup.sh propagated it into
`REACT_APP_API_URL`/`REACT_APP_WS_URL`. This is a genuine installer-trusting-
untrusted-stdin defect class (any content between `./scripts/setup.sh` and EOF
of the piped script becomes prompt input).

*Fix:* setup.sh rebinds stdin to `/dev/tty` when piped (one `exec` covers every
prompt; truly headless runs keep the bracketed defaults), plus `COLLECTOR_IP`
value validation mirroring the existing `INTERNAL_DNS` guard.

*Blast-radius audit — contained:* `host_ip.py:_valid_ip()` already rejected the
junk value (fell through to `NETPULSE_HOST_IP`); the `Collector` DB row was
clean (built from `NETPULSE_HOST_IP`, which is written by `env_set`, never
prompted); telemetry config generation resolves through the Collector row.
Only display paths (`/api/health/`, Settings→System) ever surfaced the junk.

## Authentication / access posture (as of 2026-08-01)

- **Local auth:** JWT (SimpleJWT, HS256) + login rate-limiting + audit-logged
  auth events. **TOTP MFA** is implemented (`MFADevice`, `apps/core/mfa.py`)
  with per-user enrolment and an org-wide `mfa_required_all_local` system
  setting; `reset_mfa` management command for recovery. Automation/service
  accounts that must bypass interactive MFA are an operational convention
  (dedicated service-account users), not an in-code MFA exemption.
- **SSO:** social-auth backends wired for Google, Azure AD (tenant), Okta, and
  GitHub — all via social-core's PyJWT-based validation (confirmed during the
  python-jose removal); client secrets in OpenBao, never the DB; SSO users
  minted the same JWT as local auth; local admin login always available.
- **Secrets:** OpenBao for all credentials (device, integration, backup,
  agent-PKI); write-only API fields; `LOCAL_NOTES.md` (gitignored) for
  lab-specific identifiers after the #174 repo scrub.

## Repo anonymization for open source (#174)

Real internal hostnames, IPs, a jump-host identity, and an SNMPv3 username
were scrubbed repo-wide (code, tests, docs, UI placeholders) to a generic
`site1-*` / RFC5737 convention; a negative-assertion test guards the seed
data. Engineering knowledge (firmware quirks, hardware behavior) retained.

## Open / known security-relevant items

- react-router v7 migration + immutable/swagger-ui-react (accepted-risk
  majors, above; tracked in the roadmap with revisit criteria).
- The Dependabot version-bump PR backlog (~30 pip pin-bumps for the ingest
  services) awaits a batch refresh with per-service end-to-end verification —
  version currency, not known-vulnerability, work.
- Junos config-push hardening shipped in 0.7.1 also has a defensive angle:
  pushes now use `configure private` (a concurrent operator's uncommitted
  changes can no longer be silently swept into an automated commit).

---

# spane — Security Alert Remediation (2026-06-20)

Scanner-flagged alert sweep (Dependabot + CodeQL). Full api suite (1850) passes
after the fixes; frontend rebuilt.

- **Dependabot #5/#6 — DOMPurify vulnerabilities** (transitive via
  `swagger-ui-react`, `services/frontend/package-lock.json`). 3.4.7 was affected
  by GHSA-vxr8-fq34-vvx9 (Trusted Types policy survives `clearConfig()`),
  GHSA-gvmj-g25r-r7wr (`SAFE_FOR_TEMPLATES` bypass) and GHSA-cmwh-pvxp-8882
  (`ALLOWED_ATTR` pollution via `setConfig()`). *Fix:* `npm update dompurify` →
  **3.4.11** (advisories cover ≤3.4.10); `npm audit` no longer reports dompurify;
  frontend image rebuilt.
- **CodeQL #43/#44 — exception exposure in `apps/frameworks/views.py`** (re-flag
  of #38/#39). Verified already remediated: `list`/`retrieve`/`report` route every
  `except` through `apps.core.errors.internal_error_response` (logs server-side,
  returns a generic message). No `str(e)`/`str(exc)` reaches a client in this file.
- **CodeQL #45 — exception exposure in `apps/integrations/wireless.py:190`.** The
  Mist-location endpoint returned `str(exc)` from a `ValueError` in the HTTP body.
  *Fix:* log the detail server-side (`logger.info`), return a static
  `"No floor-plan map available for this Mist site."` (404).
- **CodeQL #40 — `apps/devices/views.py`.** Verified no client exposure: the only
  `str(exc)` uses store the discovery-job failure on internal model fields
  (`DiscoveryJob.progress_message`/`error_message`), never returned in a Response.
- **General sweep** of `apps/` for `return Response(... str(e/exc) ...)` /
  `JsonResponse(...)` exposure found no further HTTP-response leaks. Remaining
  `str(exc)` occurrences are internal (model `last_error` fields, CLI health-check
  output, service-check diagnostic result dicts), not client exception exposure.

# spane — Security Audit Addendum (2026-06-19)

Application-layer review (input-validation/injection, authn/authz, WebSocket,
secrets, data-exposure, config hardening) across backend, frontend, agent, and
deploy. Remediated the verified Critical/High/Medium issues; full test suite
(1764) passes after the fixes. The 2026-06-01 automated-scan report follows
below, unchanged.

| Severity | Found | Fixed | Accepted / deferred |
|---|---|---|---|
| CRITICAL | 1 | 1 | 0 |
| HIGH | 2 | 2 | 0 |
| MEDIUM | 6 | 3 | 3 |
| LOW | 7 | 0 | 7 |

## Fixed

### CRITICAL
- **C1 — WebSocket endpoints accepted any connection unauthenticated.**
  `apps/{telemetry,alerts,devices}/consumers.py`, `config/asgi.py`. `connect()`
  called `accept()` unconditionally and the JWT was never validated on the WS
  handshake, so any unauthenticated client could stream live alerts/telemetry/
  device+topology data, bypassing DRF entirely. *Fix:* `apps/core/ws_auth.py`
  `JWTAuthMiddleware` validates a token sent as the `["bearer", "<jwt>"]`
  subprotocol; consumers reject anonymous users (close 4401); the SPA
  `useWebSocket` hook sends the token. New test `test_consumer_rejects_anonymous`.

### HIGH
- **H1 — ChatOps webhooks public & mostly unsigned** (`apps/core/chatops.py`).
  `/api/webhooks/{slack,teams,gchat,discord}/` were `AllowAny`; Teams/GChat/
  Discord had no signature step — an unauthenticated POST disclosed device/site/
  alert data. *Fix:* gated all four behind `settings.CHATOPS_ENABLED` (default
  **off**; 404 when disabled, before any parsing).
- **H2 — Unauthenticated OpenSearch bound on `0.0.0.0:9200`**
  (`docker-compose.yml`) while `DISABLE_SECURITY_PLUGIN=true`. *Fix:* bound to
  `127.0.0.1:9200`; the api reaches it over the bridge.

### MEDIUM
- **M1 — nmap option injection via discovery subnets**
  (`apps/devices/serializers.py`). `subnets`/`excluded_subnets`/`allowed_subnets`
  flowed into the nmap argv unvalidated (engineer-role nmap-flag/NSE abuse).
  *Fix:* serializer validators reject anything that isn't an IP/CIDR (notably
  leading-`-`).
- **M2 — CSV formula injection** in the audit-log and report exports
  (`apps/core/views.py`, `apps/reports/render.py`); the audit source includes an
  unauthenticated failed-login username. *Fix:* `apps.core.audit.csv_safe` +
  `_SafeCsvWriter` neutralize leading `= + - @`.
- **M3 — production TLS/cookie hardening** (`production.py`, `nginx.conf`,
  `.env.example`): `SECURE_SSL_REDIRECT` shipped on; `SESSION_COOKIE_HTTPONLY`/
  `SAMESITE`, `CSRF_COOKIE_SAMESITE`, env-driven `CSRF_TRUSTED_ORIGINS`; nginx
  TLS-1.3-only + security headers (`X-Content-Type-Options`, `X-Frame-Options`,
  `Referrer-Policy`, HSTS) on the static SPA.

## Accepted / deferred
- **A1 (MED) — SSRF via authenticated service checks** — NOT changed: reaching
  internal hosts is the product's core purpose and tests probe `127.0.0.1`; a
  blanket private-range block would break it. Recommend an opt-in
  `169.254.169.254` (cloud-metadata) denylist + connect-time IP pin.
- **A2 (MED) — no read-vs-execute RBAC** — Engineer/API roles can manage
  credentials and probe arbitrary IPs; recommend Admin-gating credential mutation
  + probe targets. Deferred (RBAC semantics).
- **A3 (MED) — Slack dev-mode signature skip** — mitigated by H1 (ChatOps off by
  default); fail-closed on the secret when ChatOps ships.
- **LOW (7):** alerting webhook URLs returned plaintext (make write_only +
  OpenBao); `infrastructure_health` public (intentional — onboarding needs it);
  no per-WS-connection rate limit; `show_credentials.py` (pre-v1.0 checklist);
  UniFi `verify_ssl=False` default; `api` holds `NET_ADMIN`; JWT refresh has no
  rotation/blacklist.

## Verified good
DRF deny-by-default; auth throttle on login/refresh; no hardcoded secrets or
credential-returning serializers; no secrets logged; deps pinned w/ CVE-floor
notes; nginx `X-Agent-*` header stripping (anti-spoof) intact; MIB/download paths
traversal-safe; no raw SQL / `eval` / `shell=True`.

---

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
