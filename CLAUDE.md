# CLAUDE.md

Guidance for Claude Code working in this repo. These instructions override default behavior.

> **Detail lives in `docs/`.** This file is a quick-reference. For full detail see
> `docs/ARCHITECTURE.md`, `docs/setup/{deployment,nat}.md`, `docs/platforms/{fortios,sonicwall,aos_cx}.md`,
> and the per-feature docs; lab-specific/credential notes live in gitignored `LOCAL_NOTES.md`. Many large feature
> designs (ChatOps, topology, availability/SLA, business-service health, TV/NOC mode, distributed
> pollers, firewall analytics, gNMI capability discovery/profiles, support bundle, multi-tenancy,
> config git-sync) are **planned, not built** — see "Planned Features" below and `docs/` for specs.

## Project Overview

> **Naming:** the product was renamed from **NetPulse** to **spane** (lowercase
> brand, tagline "unified infrastructure visibility", domain spane.app). Only
> display/brand strings changed — technical identifiers intentionally keep the
> legacy `netpulse` form: OpenBao paths (`netpulse/...`), Docker image/container
> names (`netpulse-*`), systemd units (`netpulse.service`,
> `netpulse-agent.service`), the `netpulse-net` bridge, `./netpulse.sh`, Python
> class names (`NetPulseUser`), and the GitHub repo (`travisjohnsonga/netpulse`).
> Agent binaries stay `netpulse-agent` until the next CI build (then
> `spane-agent-*`). **DB table names are NOT `netpulse_*`** — no model overrides
> `db_table`, so tables follow Django's default `<app_label>_<model>` convention
> (`core_netpulseuser`, `devices_device`, `alerts_alertevent`, …). The legacy
> name survives in the `NetPulseUser` model *class*, not its table.

spane — push-first, open-source infrastructure-visibility platform: gRPC/gNMI streaming telemetry,
config compliance, CVE intel, lifecycle, log anomaly detection, unified risk scoring.

Stack: Python 3.13, Django 6.0 + DRF + Channels (backend), React + TypeScript + Vite + Tailwind +
ECharts + Cytoscape.js + D3 + React Query + Zustand (frontend), Docker Compose (on-prem), Helm (cloud).
PostgreSQL 17, InfluxDB (time-series), OpenSearch (logs), Valkey (cache/WS broker), NATS+JetStream
(bus), OpenBao (secrets, Vault-compatible). Auth: JWT (SimpleJWT) + SSO (social-auth) minting same JWT.

## Current State (June 2026)

- Tests: ~1933 passing (services/api, in-memory SQLite). Services: 24/24 running. Python 3.13,
  Django 6.0. Frontend: React + Vite 7.

**Recently completed (agent + resilience + UI session — 2026-06-28):** all merged to `main`.
- **Service stability monitoring** (#121, role-INDEPENDENT) — `apps/agents/stability.py` +
  `WatchedServiceStatus`: the agent reports rich `ServiceStat` for operator-watched services
  (`desired_config.stability.services`); the server records state/transitions and fires/auto-resolves
  **"Service Down"** + **"Service Flapping"** `AlertEvent`s (debounce, `labels.device_id` linkage).
- **Agent liveness alerting** (#116) — `apps/agents/liveness.py`, `run_scheduler` `agent_liveness`
  task (60s): fires/auto-resolves an **"Agent Offline"** alert when `now - last_seen` exceeds the
  agent's threshold (`offline_after_seconds()`); `liveness_alerts_enabled=False` suppresses the lab box.
  (Stage 2 — DEGRADED = heartbeat-fresh-but-ingest-stale — still roadmap.)
- **Web-role functional health check** (#126, v1.5.0) — `apps/agents/functional.{go,py}`: agent-side
  HTTP/cert probe (loopback-only SSRF allowlist, `IsAllowedSelfURL`; classify 2xx/3xx healthy → 4xx
  warning → 5xx degraded → err down; cert NotAfter days). `reconcile_functional_health` fires
  site_down/site_degraded/cert_expiring(≤30d)/cert_expired. **Any-of resolution:** a web role's verdict
  is the FUNCTIONAL result (site responds + cert valid), NOT "do all of nginx/apache/httpd run." The
  RoleCard headline now leads with that verdict ("✓ Healthy · cert Nd"), not the old "2/5 services" count.
- **Agent log forwarding — Stage 1** (`apps/agents/log_publish.py`) — agent tails curated security logs
  (auth/service/kernel + allowlisted paths) → mTLS → NATS `netpulse.logs.<source>.<host>` → existing
  stream-processor → OpenSearch. ⚠️ **Built but barely flowing (~2 docs ever); under open diagnosis**
  (see Known issues). Stages 2–3 (parse/enrich, broader sources) are roadmap.
- **OS-detail + rich service detail + Services-tab table + Roles-tab functional UI** — agent reports
  os_name/os_version/os_kernel; `reported_services` carry `display_name`; ServerDetail Services tab is a
  6-col table (`tableStyles.ts` zebra), tab state in the URL (`useTabParam`).
- **Agent device-record hygiene** — the #118 device-link self-heal creates a `Device` for each agent
  (often synthetic `127.0.0.1`). These are agent **servers**, not network devices: now **excluded from
  the reachability monitor** (#133, `agent__isnull=True` + synthetic-IP) AND the **Devices list** (#136,
  list action only — retrieve/CRUD still resolve). Devices list **Connect** button → right-aligned
  plain-text **"SSH"** action. Reachability for agent hosts will come from collector→real-IP **ping/RTT**
  (roadmap), not the device-IP probe.
- **Stream-processor log/flow durability** (#134 + #138) — logs+flows JetStream consumers switched to
  `manual_ack=True`: **ack only after a successful OpenSearch bulk write**, NAK→redeliver on failure
  (per-item on partial errors), **poison-drop after `STREAM_PROCESSOR_MAX_DELIVER` (5)** deliveries so a
  doc OpenSearch always rejects can't NAK-loop/wedge the consumer; transient outages never drop (durable
  in the stream). Fixes the prior ack-on-receipt loss-on-OpenSearch-blip bug.
- **Alerts page** defaults to **actionable-only** (firing + unacknowledged) with a "show resolved &
  acknowledged" toggle (#130); **Services table** width/zebra via shared `src/lib/tableStyles.ts` (#131);
  shared **`useTabParam`** hook → consistent tab-in-URL persistence across ServerDetail/DeviceDetail/
  SiteDetail/Settings/Sites (#135); **text-only left-nav** (emoji icons dropped, #137).
- **CI / versioning** — `build-agent.yml` republishes the rolling `agent-latest` release **only on tag
  pushes** (`if: startsWith(github.ref,'refs/tags/')`, #125) so main pushes don't clobber it.
  **Versioning discipline (Option C):** minor = features, patch = fixes; **don't version dev iterations**.
  Current agent release: **v1.5.0**.

**Recently completed (feature sweep — 2026-06-20):** a large batch of features + fixes (all
committed to `main`, ~1933 api tests passing). Endpoint paths below are the real ones.

- **WAN circuit tracking** — new `apps.circuits` (migration 0001). `WanCircuit`: identity
  (name/circuit_id/type/status), provider (provider/account/contract_end_date/monthly_cost),
  bandwidth (download/upload/CIR `committed_mbps`), **ISP IP assignment** (`isp_ipv4_block`/
  `isp_ipv6_block` with CIDR validation, `gateway_ip`, `usable_ips`, `bgp_asn`, `our_bgp_asn`),
  device+interface binding, site, `alert_threshold_pct`, notes. CRUD `GET/POST/PUT/DELETE
  /api/circuits/` (filter `site`/`device`/`circuit_type`/`status`). **`GET /api/circuits/{id}/
  utilization/`** maps the bound interface name → InfluxDB `if_index`, reads `in_bps`/`out_bps`
  vs configured bandwidth → current + 24h history + peak + **95th-percentile** (nearest-rank).
  Scheduler `circuit_checks` (15m, `CIRCUIT_CHECK_INTERVAL_S`): standing **"High WAN Utilization"**
  alert (>threshold, auto-resolves) + **"WAN Contract Expiring"** at 90/60/30/14/7 days (deduped).
  Frontend: `/circuits` page (cards w/ live ↓/↑ util bars + P95 + IP/contract/cost) under
  Network in the sidebar; add/edit modal; the Site-detail **WAN Circuits tab is populated**
  (was a placeholder). `tests/test_circuits.py` (23).
- **Manual topology links** — `ManualTopologyLink` (devices migration 0030): device_a/interface_a
  ↔ device_b/interface_b, `link_type` (ethernet/fiber/wan/lacp/mgmt/virtual/other), `speed_mbps`,
  `description`, `created_by`; unique per (a,iface_a,b,iface_b). CRUD `/api/topology/manual-links/`
  (filter `device_id`/`site_id`), create/update/delete **audit-logged** (core migration 0011 adds
  the event types). `topology.build_manual_edges()` emits separate edges flagged `manual:true`;
  the topology endpoint appends them. Frontend: `🔗 Add Manual Link` on the Topology page, manual
  edges drawn **dashed + coloured by type** (`MANUAL_LINK_COLORS`), Settings→Network **Manual Links**
  mgmt page + sidebar, device-detail menu item (pre-fills this device). `tests/test_manual_topology.py` (10).
- **Periodic environment/PoE collection** — `apps/telemetry/environment_poll.py` scheduler task
  (5m, `ENVIRONMENT_POLL_INTERVAL_S`) REST-collects AOS-CX temp/fan/PSU + PoE and writes the SAME
  `device_environment` InfluxDB schema the Environment tab reads (so it's stored for alerting/
  trending even when the tab is closed). Standing **"High PoE Usage"** alert (>`POE_ALERT_THRESHOLD_PCT`,
  default 80; auto-resolves), rule seeded. PoE summary from per-port `get_poe_status()`
  (used=Σdrawn, budget=Σallocated). `tests/test_environment_poll.py` (8).
- **Compliance score unified + on-demand/scheduled runs** — `DeviceComplianceScore` (compliance
  migration 0009, OneToOne) stores the **weighted** score (template 50% + interface 30% + role 20%
  + startup); the device-list subquery now reads it so the list and the Compliance tab show the
  SAME number. `engine.run_compliance_for_device()` **always** persists it (opt-out for callers that
  reconcile first). On-demand: `POST /api/compliance/run-all/` (background, progress in shared
  Valkey cache) + `GET /run-all/status/` + `POST /run/{device_id}/`; UI triggers on the Compliance
  settings page (with progress), the device Compliance tab ("Run Now"), and a device-list bulk
  "Run Compliance". **Daily** scheduled fleet run at 03:00 (`COMPLIANCE_RUN_HOUR`, hour-gated +
  same-day deduped — after the 02:00 backup). `tests/test_compliance_run.py` (11),
  `test_compliance_schedule.py` (6), `test_device_score.py` (+6).
- **Alerts bulk actions + state tabs** — `POST /api/alerts/events/bulk-acknowledge/` + `bulk-resolve/`
  (`{updated,failed,errors}`), `GET /api/alerts/events/summary/` (counts). The model has no ACK
  state, so "acknowledged" is derived (firing + has `AlertAcknowledgement`); `?state=firing|
  acknowledged|resolved` handled in `get_queryset`. Frontend: checkbox column + select-all
  (indeterminate), bulk toolbar, All/Firing/Acknowledged/Resolved tabs with counts, 5+ confirm,
  keyboard shortcuts (a/r/Esc/⌘A).
- **Flow Analytics DNS enrichment** — `POST /api/flows/resolve/` (inventory-first, then parallel
  reverse DNS, Django-cached 5m, ≤100 IPs/req) + `POST /api/flows/resolve/clear-cache/` (admin).
  Frontend: "🔍 Resolve hostnames" toggle (localStorage + `?resolve=1` URL), hostname-with-IP-on-hover.
- **flow-threshold alert fix** — full exporter IP (was truncated to the first octet by `split('.')`
  on the NATS subject), sane Mbps (0-duration records were /1ms → 100s of Gbps; floored at 1s), and
  device-hostname + top-talker enrichment in labels/annotations.
- **Per-user temperature unit (°C/°F)** — `UserPreferences.temperature_unit` (core migration 0010);
  flows through `GET/PUT /api/users/me/` + `/api/users/me/preferences/`. Frontend: Profile→Display
  toggle (instant localStorage + background save), `unitsStore` + `useTemperature()`, applied across
  the device Environment tab (tile, sensors, 24h chart axis/tooltip/thresholds), Wireless, Console.
- **Version display fix** — `/api/health/[infrastructure/]` returned `"unknown"`; `_netpulse_version()`
  now resolves `SPANE_VERSION`/`NETPULSE_VERSION` env → bind-mounted `/app/VERSION` → `settings.VERSION`
  (`1.0.<count>`). Root `VERSION` file (bind-mounted into the api container, so it updates without a
  rebuild). Shown in Settings→System + the TV footer (sidebar `VersionBadge` already worked).
- **Safe update flow** — `./netpulse.sh update` (`scripts/update.sh`): snapshot git tag, refuse dirty
  tree, ff-only pull, **`.env` back-fill** from `.env.example`, **DB backup** (`.update-db-backup-*.sql.gz`),
  rebuild changed services, **explicit migrate**, NAT re-apply, **health verify** (rollback hint on
  failure), append to `.update-history.log`. Plus `show-version` + `rollback [tag]`. Stamps
  `SPANE_VERSION` into `.env`. `CHANGELOG.md` added.
- **Security** — Dependabot **form-data** 4.0.5→4.0.6 (CRLF, #8) + **js-yaml** 4.1.1→4.2.0 (DoS, #7)
  via `package.json` overrides (DOMPurify was the prior sweep). Exception-exposure (CWE-209) is fully
  scrubbed via `safe_detail`/`internal_error_response`; enforced by `tests/test_security.py::
  TestNoExceptionExposure` (AST guard over every `apps/**/views.py`), `scripts/check_exception_exposure.py`,
  a pre-commit hook (`.pre-commit-config.yaml`), and a CI workflow (`.github/workflows/security-checks.yml`).

**Recently completed (security-alert sweep — 2026-06-20):** Dependabot **DOMPurify**
(transitive via `swagger-ui-react`) bumped 3.4.7 → **3.4.11**, clearing
GHSA-vxr8-fq34-vvx9 / -gvmj-g25r-r7wr / -cmwh-pvxp-8882 (`npm audit` clean for
dompurify; frontend rebuilt). CodeQL **exception-exposure** fixes:
`apps/integrations/wireless.py` Mist-location endpoint no longer returns
`str(exc)` (logs detail, returns a static 404 message); `apps/frameworks/views.py`
confirmed already routed through `internal_error_response`; `apps/devices/views.py`
confirmed clean (its `str(exc)` only stores to internal `DiscoveryJob` fields, not
HTTP responses). Whole-`apps/` sweep found no other `Response(... str(exc) ...)`
leaks. 1850 api tests pass. See `SECURITY-REPORT.md` (2026-06-20 entry).

**Recently completed (overnight platform/security session — 2026-06-19):**
- **TV/NOC dashboard mode** — `/tv` launcher (large tiles + auto-rotation builder) and chrome-free
  fullscreen screens `/tv/{network,wireless,security,ops,sites,servers,compliance}` rendered OUTSIDE
  the app shell (auth-required, no sidebar/topnav, dark high-contrast, large fonts, per-screen
  auto-refresh). `/tv/rotate?screens=…&interval=…` cycles selected dashboards with a next-screen
  countdown + progress bar. Reuses existing endpoints (devices/alerts, wireless summary, audit-log,
  collection-health/agents/checks, servers, framework coverage). `TVLayout` shared chrome; sidebar
  "TV Dashboards" entry. Frontend-only. `docs/tv-dashboards.md`.
- **Platform backup & restore** (`apps.backup`, migration 0001) — `BackupConfig` singleton +
  `BackupRecord`; destinations local/SCP/Git/S3 (secrets in OpenBao `spane/backup/{scp,git,s3,
  encryption}`, never DB/responses). **Mandatory AES-256-CBC + PBKDF2 600k-iter encryption** for any
  backup containing secrets (Database/OpenBao/Certs): password required (≥12 chars), passed to
  `scripts/backup.sh` via env (never argv/ps), never stored (only an optional hint). Plaintext
  `…manifest.json` written beside each `…enc.tar.gz`. `scripts/{backup,restore}.sh`; `./netpulse.sh
  {backup,restore,list-backups}`; scheduler `backup` task (hour-gated + same-day-deduped; skips when
  encryption required but no stored password). API `/api/backup/{config,run,records,records/{id},
  test-connection,download/{id}}`. `docs/admin/backup.md`. `tests/test_backup.py`.
- **Production resilience/hardening** — health **watchdog** (`scripts/watchdog.sh`, cron 5-min via
  `./netpulse.sh install-watchdog`; restarts unhealthy containers, unseals OpenBao via `init_openbao`,
  fd-leak preemptive restart; logrotate; `watchdog-status`/`remove-watchdog`); `x-app-ulimits` anchor
  sets `nofile=65536` on all 17 app services; gunicorn **worker recycling** (`--max-requests 1000`
  `--max-requests-jitter 100` `--keep-alive 2` `--worker-tmp-dir /dev/shm`, `--timeout` kept at 120s
  for ~1-min report PDFs).
- **AgentCertAuthentication O(1) fix** — was an O(n) scan + per-request Python normalize over all
  agents (file-descriptor exhaustion under load). New indexed `Agent.cert_serial_normalized` (migration
  0005) kept in sync by `save()`; single indexed lookup.
- **Security audit remediation** (see `SECURITY-REPORT.md` 2026-06-19 addendum) — **WebSocket JWT auth**
  (`apps/core/ws_auth.py` `JWTAuthMiddleware`; token via `["bearer","<jwt>"]` subprotocol; consumers
  reject anon 4401; SPA `useWebSocket` sends it) — fixes unauthenticated realtime data leak; ChatOps
  webhooks gated behind `settings.CHATOPS_ENABLED` (default off); OpenSearch port bound to `127.0.0.1`;
  nmap subnet-injection validators on `DiscoveryJobSerializer`; CSV formula-injection guard
  (`csv_safe`/`_SafeCsvWriter`) on audit-log + report CSVs; production TLS/cookie hardening
  (`SECURE_SSL_REDIRECT` shipped on, SameSite/HttpOnly, `CSRF_TRUSTED_ORIGINS`); nginx TLS-1.3-only +
  SPA security headers.
- **Config-collection fixes** — `collect_one` early-returns `not_supported`/`skipped` (no log row) for
  cloud/controller-managed platforms (UniFi/Mist AP/UDM/SW/GW + unknown/blank); `collect_all_configs`
  excludes `SKIP_CONFIG_PLATFORMS` + wireless-ap/wireless-controller roles; discovery `is_infra_hostname`
  rejects this stack's Docker container names; `prune_unsupported_collection` management command;
  Collection Health UI note.
- **Reports** — Compliance Summary preview now renders HTML (fleet stats + by-site/role/platform score
  tables) instead of raw JSON.

**Recently completed (agent + servers session):** spane Agent end-to-end —
OpenBao PKI auto-setup (`setup_agent_pki`: `pki` mount + "spane agent ca" EC
P-384 root + `agent` role + policy; CA at `GET /api/agents/ca-certificate/`) ·
nginx **mTLS termination** for agent ingestion (server-level `ssl_verify_client
optional` against the agent CA, enforced per-location; `X-Agent-Cert-Serial` →
`AgentCertAuthentication`, serial normalized nginx↔OpenBao; CA published to the
shared ssl volume) · agent transport graceful degradation (mTLS when cert
present, else plain/Bearer) · `/agent/{install,download/<platform>}` Django
endpoints + `-insecure` flag · enrollment-token **OS selector** (Linux/Windows/
Both → tailored install commands) + self-signed checkbox · **server role
assignment** (`AgentRole` through-model; manual + auto-detect from reported
services + config-declared via role-checks) · dedicated **Servers page**
(`/servers` list + `/servers/:id` detail with CPU/Memory/Disk/Network/Roles/…
tabs, `/api/servers/` API reading agent metrics from InfluxDB). Agent docs under
`docs/agents/`.

**Recently completed (agent install + security session):** Agent install/serving
now works **end-to-end**. nginx `location /agent/` proxy added (requests were
falling through to the SPA → `index.html`); the repo `agent/` dir is bind-mounted
read-only at `/agent` (= `AGENT_DIR`) on the api service, so `GET /agent/install`
serves `scripts/install.sh` and `GET /agent/download/{linux-amd64,linux-arm64,
windows-amd64}` serve the CI-built binaries (verified ELF / ELF-arm64 / PE32+,
HTTP 200 through public HTTPS). Verified one-liner:
`curl -fsSL [-k] https://server/agent/install | sudo bash -s -- --server URL
--token TOKEN [--insecure]`. Binaries come from the `build-agent.yml` CI artifact
(`agent-binaries`, all 3 platforms), gitignored in `agent/dist/`. ·
**Linux systemd install** — `-install-service` now works on Linux (writes a
hardened `/etc/systemd/system/netpulse-agent.service`: `NoNewPrivileges`,
`ProtectSystem=strict`, non-root `netpulse-agent` user); the installer is
re-run/upgrade-safe (stops the service + removes the old binary first). ·
**Graceful agent re-enrollment** — re-running the installer reuses the existing
agent record and rotates its cert (HTTP 200) instead of 500-ing on the device
OneToOne; revoked hosts get a fresh record; residual conflicts return **409**
with a revoke-then-retry message (no more 500). · **Agent role auto-enable** —
the metrics response returns `assigned_roles` + `collection_config{services,
role_checks_enabled}`; the Go agent reconciles and persists this, so assigning a
role in the UI auto-enables its checks on the agent's next check-in (no manual
config edit; the Roles tab shows a notice). · **Vite 5.4.21 → 7.3.5**
(+`@vitejs/plugin-react` ^5.2.0) — fixes GHSA-4w7w-66w2-5vf9 (dev-server `.map`
path traversal); 0 npm-audit vulnerabilities. · **CodeQL HIGH alerts resolved** —
mibs path traversal (reject-not-strip + `os.path.basename`/realpath containment),
meraki credential logging (log constants, not payload-derived values), audit
failure-path logging (event_type only — never the exception object),
integrations exception exposure (generic message via the SMTP test endpoint);
`build-agent.yml` got least-privilege `permissions`, the Node24 opt-in
(`FORCE_JAVASCRIPT_ACTIONS_TO_NODE24`), `cache-dependency-path: agent/go.sum`,
and a `workflow_dispatch` trigger.

### Remote Collector Subsystem (status — moved from "planned, no code")

Maturity: **[VALIDATED]** proven end-to-end · **[BUILT]** committed, not yet proven end-to-end ·
**[PLANNED]** not built. Full design: `docs/ARCHITECTURE.md` §10; ops: `docs/collectors/runbook.md`;
gates: `docs/collector-production-gates.md`; proofs: `scripts/t0`–`scripts/t3`.

- **[VALIDATED]** Transport substrate — NATS leaf + edge JetStream buffer, hub sources by acked seq
  (cut→buffer→replay, zero loss/dup); mTLS leaf (TLS1.3, handshake_first, advertise+no_advertise);
  operator/JWT per-collector accounts; revoke-without-reload; inter-NATS account-mapped telemetry to
  the untouched internal bus (separate operator-mode collector-hub); signing-key rotation both modes
  (planned zero-breakage; compromise stage-first, ~2.4s push-bounded for 16 accts).
- **[BUILT]** Central side (committed, unit-tested, no live agent yet): enrollment (token→api-key +
  account + best-effort PKI cert), config-DOWN (per-collector non-secret bundle of cred-path refs +
  checks + sha256 rev → JetStream KV), single-authority resolution via `Site.default_collector`
  (`resolve.effective_collector` / `devices_for_collector`). Secret-broker (Option A): authz logic +
  live least-privilege OpenBao policy (read-only/no-list, verified) + fail-closed are VALIDATED; its
  **identity-from-transport NATS routing is BUILT, not yet proven end-to-end** (the blocking gate).
- **[PLANNED]** `services/collector` agent (forwarder + buffer/replay + broker client),
  `docker-compose.collector.yml`, `setup.sh` role selection.
- **Remaining before prod creds flow:** (1) broker identity-wiring end-to-end proof (A-can't-fetch-B
  over the real transport); (2) the agent; (3) packaging + setup.sh role; (4) an api rebuild to apply
  **migration 0004** (collector identity fields — committed, not yet migrated on running stacks).

**Recently completed (UniFi/NetBox/DNS/agent session):**
- **UniFi sync IP protection** — new `Device.ip_locked` boolean (migration **0027**); when true, UniFi
  sync skips the `management_ip`/`ip_address` update in both `_import_device` and
  `update_linked_device_host` (non-IP fields still update). Lock/unlock toggle in the device edit modal;
  amber lock indicator beside Management IP on the device overview. `_host_to_controller_fields`
  (`unifi_cloud.py`) now **prefers a LAN IP over WAN** — skips `wans[].ipv4` addresses when picking the
  mgmt IP (reported primary if not WAN → first non-WAN IPv4 in `ipAddrs`), WAN only as last resort. Fixes
  the cloud host record reporting a console's WAN IP and clobbering a curated LAN mgmt IP.
- **NetBox v2 tokens only (v1 removed)** — requires **NetBox 4.5+**. Two-field UI: **API Key ID** (`nbt_…`)
  + **API Token** (secret), combined backend-side as `{key}.{secret}` and stored in OpenBao under
  `api_key`. `_get_auth_header()` always returns `Bearer {token}`. Serializer validates the `nbt_` prefix
  (tailored "legacy v1 token" error for 40-char-hex values) and a non-empty secret; payloads send
  `api_key` + `api_token`.
- **Internal DNS fix** — the `x-internal-dns` compose anchor was hardcoding a lab resolver. Now reads
  `${INTERNAL_DNS:-8.8.8.8}` (+ 8.8.8.8 fallback) and `${INTERNAL_DOMAIN[2]:-}` for `dns_search`.
  `INTERNAL_DOMAIN2` supported as a second search domain; `setup.sh` auto-detects DNS server + both
  domains from `resolvectl status`; `.env.example` ships **empty defaults** (no lab IPs/domains
  committed). Verified: api `resolv.conf` shows `ExtServers [<INTERNAL_DNS> 8.8.8.8]`, external
  resolution works.
- **Agent PKI CA cert sharing fixed** — api Dockerfile pre-creates `/app/ssl` with correct ownership;
  `entrypoint.sh` `mkdir`+`chmod` before `setup_agent_pki`; nginx waits for the real CA cert (detects the
  placeholder CN); the `ssl-certs` volume is shared between api and frontend automatically (no manual fix).
- **Agent metrics flowing (200 OK)** — mTLS working end-to-end; cert-serial normalization correct;
  re-enrollment graceful (**409**, not 500).
- **Agent binary distribution** — published via **GitHub Releases** (download views redirect, fallback to
  the `agent/dist/` mount); no `gh` CLI required for users; `agent/dist/` gitignored; `build-agent.yml`
  triggers on `branches: [main]` push.

**Recently completed (Mist session):** **Juniper Mist wireless integration** (cloud-only, mirrors the
UniFi pattern) — `MistIntegration` singleton + `MistSite` models (integrations migration **0013**;
device-platform choices `mist_ap`/`mist_sw`/`mist_gw` → devices migration **0028**; AuditLog
`mist_sync` event → core migration **0009**). API token stored in OpenBao at `netpulse/integrations/mist`
(key `api_token`, write-only). `MistClient` (api.mist.com, `Authorization: Token …`) + `mist_sync`
(org → sites → devices, merges `/devices` inventory with `/stats/devices` for ip/version/status, keyed
by MAC→IP→hostname, honours `ip_locked`). Endpoints `GET/PUT /api/integrations/mist/`,
`POST …/test/`, `POST …/sync/`, `GET …/sites/` (singleton `MistViewSet`, explicit url mapping).
Scheduler `mist_sync` task (6h, `MIST_SYNC_INTERVAL_S`, skips when no enabled account). Frontend
Settings → Integrations → Mist modal (`MistSettingsModal`: token save, Test Connection showing
email/org, Sync Now, discovered-sites table). State is DB-backed so a connected account survives an
api restart. Tests: `tests/test_mist.py` (21).

**Recently completed (reports session — 2026-06-14):** **Reporting subsystem** — new app
`apps.reports` (migration 0001; `GeneratedReport` history + `ReportSchedule`). Two reports:
**Compliance Summary** (`build_compliance_summary` — fleet weighted scores grouped by site/role/platform
+ findings-by-severity + startup-mismatch list; reuses `device_score` with a shared role-consistency
cache so a fleet report evaluates each rule once, not per device) and **Daily Operations**
(`build_daily_ops` — security/login events from AuditLog, device availability from
`Device.unreachable_since`, compliance events from AlertEvent, config changes from DeviceConfig,
collection health from ConfigCollectionLog, agent health, alerts summary). Renderers: PDF (reportlab,
branded header/alternating-row tables/score bar/page numbers), CSV, JSON, and HTML (daily-ops). APIs:
`POST /api/reports/{compliance-summary,daily-ops}/` (format-aware → file download or JSON body),
`GET /api/reports/` (history), `GET /api/reports/{id}/download/`, per-type
`…/schedule/` (GET/POST) + `…/schedules/{id}/` CRUD. Reports stored under `MEDIA_ROOT/reports/{y}/{m}/`
(new `MEDIA_ROOT` setting; served only via the authed download endpoint). Scheduled delivery wired into
`run_scheduler` (`scheduled_reports` task, hour-gated + same-day-deduped, emails the artifact via the
SMTP integration). Frontend `/reports` page (sidebar "Reports"): two report cards + Generate-Now modal
(format/group-by/date) + **Preview** modal (in-browser daily-ops preview with expandable, syntax-
highlighted config diffs) + Schedule modal (frequency/hour/day/format/recipients + existing-schedule
management) + Recent Reports table. The Daily Operations `config_changes` carry the **full unified diff**
(computed on the fly from the previous vs current `DeviceConfig.content`, capped at 600 lines) plus
site/role/platform, `previous_backup_at`/`current_backup_at`, and a short derived `diff_summary`; PDF
renders a summary table then per-device colour-coded diffs (green/red/grey, Courier), HTML mirrors it,
and scheduled emails carry a "Quick Summary" body (`email_content`) with per-device change lines + the
PDF attached (`generate()` returns the data dict alongside the file). `tests/test_reports.py` (21). NOTE: the Compliance Summary's
role-consistency VLAN checks hit live devices over REST (existing behavior), so a full-fleet PDF can
take ~1 min; reports are on-demand/scheduled so this is acceptable. Daily-ops downtime is derived from
`Device.unreachable_since` (no discrete outage-history table yet) — start-of-outage accurate,
intra-day recovery approximate.

**Recently completed (Operations report overhaul — supersedes much of the block above):** the Daily
Operations report's data queries were corrected and the report was substantially expanded.
`apps/reports/daily_ops.py` now builds via `build_ops_report(period, end_date, site_ids)`;
`build_daily_ops()` is a thin `period="daily"` wrapper (back-compat). Section-by-section:
- **Device availability** is reconstructed from `device-unreachable` **AlertEvent** history (created_at =
  down, resolved_at = recovery, still-FIRING = still down) — captures outages that recovered the same
  day, which `Device.unreachable_since` (reset on recovery) could not.
- **Security events (§1)** = authentication **failures FROM network devices**, mined from OpenSearch
  syslog (`netpulse-logs-*` via `apps.logs.views._execute`). Failures are queried **separately** with
  `must_not` on success + collector-noise (`host key verification`) phrases — successes can outnumber
  failures ~400:1 and would otherwise crowd them out of the size cap. Bare `radius`/`tacacs` are NOT
  failure patterns (they match "succeeded with RADIUS"). Grouped by user with brute-force / multi-device
  flags; **success-after-failures** flags only ≥3 failures on the SAME device within 15 min before a
  success. Degrades gracefully (empty + "forward TACACS+/RADIUS syslog" note) when OpenSearch is down.
- **spane Access Events (§8, new)** = spane's OWN audit (`AuditLog`): failed logins, after-hours logins,
  new source IPs, admin/config actions — routine successful logins are NOT listed.
- **Compliance (§3)** uses **as-of scoring** from `ComplianceTemplateResult` (latest per device ≤ report
  day, no live calls) → fleet score/grade + day-over-day trend (degraded/improved), unsaved-config
  device list (hostname/site/last-checked), per-device top issues. (`ComplianceResult` has no
  score/checked_at — don't use it for scores.)
- **Service Check Failures (§4, new)** from `CheckResult` (down/degraded), grouped per check with
  duration stats and **correlation** to device outages (±5 min).
- **Collection Health (§6)** adds a per-status breakdown (success/unchanged/timeout/auth_failed/…) +
  success rate. (`collect_one()` already writes `ConfigCollectionLog` on every exit path.)
- **Alerts (§7)** gains a critical/high event list.
- **Reporting periods:** daily / weekly / monthly / quarterly (`PERIOD_OPTIONS`, `_period_bounds`).
  Multi-day periods add period-over-period **comparison** and per-day **trend** series (compliance avg
  via ORM `TruncDate`, downtime bucketed from outages, security via one OpenSearch `date_histogram`).
  New endpoint **`POST /api/reports/ops/`** `{period,end_date,format,site_ids}` (`OpsReportView`);
  `/api/reports/daily-ops/` still works. `ReportSchedule.Frequency` gains **QUARTERLY** (reports
  migration **0002**; fires the 1st of Jan/Apr/Jul/Oct); the report period rides in
  `schedule.parameters`. Period-aware filenames (`spane-{daily,weekly,monthly,quarterly}-ops-*`).
- **PDF redesign** (`render.daily_ops_pdf`): page-1 executive summary (branded title + four
  colour-coded stat boxes + numbered one-line section summaries + comparison + trend charts), then
  **conditional** detail pages (compliance always; others only when they have content). Branded
  header/footer on every page ("CONFIDENTIAL — Internal Use Only", page numbers), navy section bars,
  accent/alternating tables, grade colour chips, 24h outage timeline. New palette + helpers
  (`_daily_styles`, `_section`, `_dtable`, `_stat_box`, `_page_decorator`, `_outage_timeline`,
  `_trend_chart`).
- **Report history delete:** `GeneratedReportViewSet` is now `DestroyModelMixin` (DELETE
  `/api/reports/{id}/`, removes the file too) + `POST /api/reports/bulk-delete/` `{ids:[…]}`; the
  frontend Recent Reports table has per-row + select-all checkboxes and a bulk "Delete N selected".
- **Security (CodeQL exception-exposure):** `_generate_and_respond` scrubs the `ValueError` via
  `safe_detail`; `frameworks/views.py` (list/retrieve/PDF) and the devices `compliance` endpoint wrap
  their live-data/PDF operations in `internal_error_response` so no exception text reaches clients.
  (esbuild Dependabot alert was already resolved — lockfile is 0.25.12 with an `overrides` pin.)
- `setup.sh` validates `INTERNAL_DNS` and clears a non-IP value (e.g. a literal `10.x.x.x`) so a bad
  placeholder can't trigger docker-compose's "invalid DNS address" boot failure.
`tests/test_reports.py` ≈49 tests.

**Recently completed (config-compliance session — 2026-06-14):**
- **AOS-CX config-collection hang fixed** — Netmiko's interactive `send_command` blocked on the AOS-CX
  `--More--` pager; config backup now uses a dedicated path (`collect_aos_cx_config`): REST
  `GET /fullconfigs/running-config` first, then a paramiko `exec_command` SSH fallback (bounded 15s/30s) —
  Netmiko is avoided for aos_cx. `tests/test_aos_cx.py`.
- **Config-collection audit log** — new `ConfigCollectionLog` (configbackup migration **0003**): one row per
  *attempt* on every outcome (success/unchanged/failed/timeout/auth_failed/empty) with `duration_ms`,
  transport `method` (rest/ssh/netconf/netmiko via a thread-local consumed in `collect_one`),
  `bytes_collected`, `config_changed`. APIs: `GET /api/configbackup/collection-log/` (filters
  device_id/status/since, paginated), `GET /api/configbackup/collection-stats/` (24h summary +
  never-collected + failing devices + `unsaved_configs`), `GET /api/devices/{id}/collection-log/`. Stats in
  `apps/configbackup/stats.py`. Frontend: history table on the device Configuration tab, a Collection Health
  panel (Settings → Compliance → Config Health), and a dashboard widget. `tests/test_collection_log.py`.
- **Weighted device compliance score** (`apps/compliance/device_score.py`) — replaces the template-only
  average with a renormalised weighted score over the components that apply: Template **50%**, Interface
  Rules **30%**, Role Consistency **20%**, Running/Startup Match **20%** (renormalised by the sum of present
  weights). Grade A/B/C/D/F. `GET /api/devices/{id}/compliance/` now returns `score`/`grade`/`breakdown` +
  `interface_rule_findings` (with the failing `interface_config` block + a platform `suggested_fix` from
  `SUGGESTED_FIXES`) + `role_consistency_findings` + `startup_status`; `overall_score`/`results`
  (template-only) retained for back-compat. Compliance tab redesigned (score header + breakdown bars,
  Template / Interface Rule / Role Consistency sections, copyable fixes). `tests/test_device_score.py`.
- **Running-vs-startup config check** — `check_running_startup_match(device)` in
  `apps/compliance/collector.py`: AOS-CX compares running vs startup over REST (`AOSCXClient.get_startup_config`),
  Cisco ios/ios_xe use `show archive config differences`. `collect_one` reconciles after each collection,
  stamping `DeviceConfig.startup_match`/`startup_diff`/`startup_checked_at` (configbackup migration **0004**),
  and fires a standing MEDIUM **"Startup config not saved"** alert (`alert_type=config_unsaved`, deduped,
  auto-resolves). Surfaced in the compliance tab (green/red diff + copyable `write memory`), a dashboard
  "Unsaved Configs" banner, and a saved/unsaved badge on the Configuration tab. `tests/test_startup_config.py`.
- **Regulatory compliance reporting** — new app `apps/frameworks`: `RegulatoryFramework` + `FrameworkControl`
  (migration 0001 + data-seed 0002; `seed_frameworks`). Six frameworks with representative control catalogs
  (SOX ITGC, ISO 27001, NIST CSF 2.0, PCI-DSS 4.0, HIPAA Security Rule, CIS Controls v8). An evidence engine
  (`evidence.py` collectors → `engine.py`) maps **live spane data** (asset inventory, config compliance,
  backups, change audit, running/startup, CVEs, OS lifecycle, OpenBao secrets posture, RBAC, audit logging,
  TLS, segmentation) to control statuses (satisfied/partial/gap/n-a) with a renormalised coverage score.
  APIs: `GET /api/frameworks/`, `GET /api/frameworks/{key}/`, `GET /api/frameworks/{key}/report/` (PDF
  evidence package via **reportlab**, added to requirements). Frontend page at `/compliance` (sidebar
  "Compliance"): framework cards + coverage, drill-in to controls + evidence, download PDF.
  `tests/test_frameworks.py`. NOTE: control catalogs are representative subsets mapped to available signals,
  not verbatim reproductions of the standards; PARTIAL controls may require manual attestation.

**Recently completed (this session):** Alert expanded panels render config diffs with green/red
syntax highlighting (reuses the `DiffViewer` component) · LLDP neighbors page added to the sidebar ·
LLDP neighbors now persisted to the `LLDPNeighbor` table (scheduler every 30 min + manual
`collect_lldp` command) · AOS-CX LLDP collection fixed for FL.10.13 firmware (per-interface API, not
`/lldp_neighbors_info`; two methods with auto-fallback) · LLDP capability parsing fixed
(comma-separated `"Bridge, Router"` → `["bridge","router"]`) · LLDP undiscovered-neighbors page
(capability filters, hostname search, default excludes phones/workstations) · AOS-CX syslog severity
keyword fixed (`info`, not `informational`) · OS-version policy (pre-populated from inventory, opt-in
scoring, most-urgent precedence: prohibited > deprecated > preferred > approved) · multi-collector
service checks (model + aggregation + API; central engine attributes to the default collector until
the distributed agent ships) · Settings reorganized into tabbed pages (Users & Access, Alerting,
Network Devices, Compliance, System) · Audit log now uses real data (40+ event types, CSV export) ·
Topology shows ALL devices (offline = red) · all CodeQL HIGH alerts resolved · ReadTheDocs (MkDocs +
Material theme, manual GitHub webhook).

**Recently completed:** default admin password `spane1!` + forced change on first login ·
ALLOWED_HOSTS auto-detection in setup.sh · web UI defaults to ports 80/443 · CodeQL workflow + all
HIGH alerts fixed · MkDocs docs on ReadTheDocs · Email/SMTP integration (Settings → Integrations →
Email) · 24h periodic hostname re-check (SNMP sysName/DNS) · generic seeded hostname-rule + site
examples · removed the top-level discovery pending-approval panel · profile page surfaces API field
errors (parseApiErrors) · UniFi multi-controller support + Site Manager cloud auto-discovery ·
SiteCredential assignments (per site, optional role) · NetBox import preview endpoint + UI · host-IP
detection prefers NETPULSE_HOST_IP (not the container IP).

**Pending / next session:** **AOS-CX REST API migration** — migrate all collection (interfaces, ARP,
environment, VLANs, PoE, routes) from SNMP/SSH to the REST API, with SNMP kept as fallback; priority
order: (1) system info, (2) interface list + stats, (3) ARP table, (4) environment/sensors, (5) VLANs,
PoE, routes (see the AOS-CX REST notes in the platform doc + the Platform Support section below) ·
test UniFi against a real controller · UniFi device sync once local controller credentials are added ·
SonicWall v7 config backup still requires the built-in `admin` account · collector IP: fresh installs
may store the container IP unless `NETPULSE_HOST_IP` is set in `.env` · marketing website (post v1.0) ·
product-name decision before v1.0. NOTE: device IP fields were investigated for consolidation and
intentionally KEPT BOTH — `ip_address` is the required/unique identity IP (dedup, ARP/flow
correlation), `management_ip` is the optional OOB/management override (connection code uses
`management_ip or ip_address`). They serve distinct purposes; not merged.

**Known issues:** a fresh install can store the container IP as the collector IP if `NETPULSE_HOST_IP`
isn't set (setup.sh now sets it; `register_local_collector` self-heals a 172.16/12 value) · SonicWall
v7 config backup needs the built-in `admin` (named accounts get 401) · OpenBao token can be lost after
a factory reset (re-unseal/re-init) · **agent log forwarding is under-flowing** — Stage 1 is wired
(agent → NATS → stream-processor → OpenSearch) but only ~2 docs have ever landed; open diagnosis (the
device-syslog path flows fine, so it's specific to the agent relay) · **the lab is VMware-on-PC** — the
host PC sleeping/rebooting silently drops the lab device VMs off the virtual network (e.g. all telemetry
froze 2026-06-26 for ~43h until the VMs were powered back on). This is an **environment artifact, not
spane behavior**: when every stream freezes at the same instant and the host can't even ARP the lab
subnet, suspect the PC/VMs before the stack. `fix-nat` does nothing while ARP fails — the devices must be
powered on first.

**Recently FIXED (was a known issue):** the stream-processor **ack-on-receipt** loss-on-OpenSearch-blip
bug — logs/flows now ack only after a successful write, NAK→redeliver on failure, poison-drop after N
attempts (#134 + #138).

## Architecture (brief)

Ingest services publish to NATS → stream-processor fans out to InfluxDB/OpenSearch/PostgreSQL.
Ingest ports: gRPC/gNMI 57400, Syslog 514/601, NetFlow 2055, sFlow 6343, SNMP trap 162.
Only external-facing ports exposed; infra services (postgres/influxdb/opensearch/valkey/nats/openbao)
are internal-only on the `netpulse-net` bridge. Protobuf `*_pb2*.py` are gitignored — regenerate from
`.proto`. Push-first telemetry; SNMP polling is the fallback. Multi-tenant-ready (Phase 1 = tenant
isolation, planned). Security-first: OpenBao for ALL credentials, never plaintext anywhere.

## Service Layout / Images

Build contexts: `./services/api` (api, websocket, stream-processor, config-manager, alert-engine,
cve-engine, lifecycle-engine, security-engine, scheduler, check-engine, reachability-monitor),
`./services/frontend`, `./services/ingest*` (grpc, snmp, syslog, flow, otlp, api-poller).
Each api service gets its OWN image (`netpulse-<service>`) — they do NOT share one image.

## Key Commands

Full deployment/dev-workflow detail: `docs/setup/deployment.md`.

```bash
# First run
cp .env.example .env && cp docker-compose.override.yml.example docker-compose.override.yml
./scripts/setup.sh                      # interactive first-run config (writes .env, never commits)

# Stack control (also via systemd: sudo systemctl {start,stop,restart,status} netpulse)
docker compose up -d                    # full stack
docker compose up -d postgres influxdb opensearch valkey nats openbao   # infra only
docker compose logs -f --tail=100 <svc>

# Rebuild after code changes (image is baked — host edits do NOT hot-reload)
./netpulse.sh rebuild-api               # rebuild all api images + recreate (--no-deps)
./netpulse.sh rebuild-frontend
./netpulse.sh fix-nat                   # re-apply Docker MASQUERADE NAT (run with sudo)

# Tests (in-container, in-memory SQLite)
docker compose exec api python -m pytest -q
docker compose exec api python -m pytest tests/test_checks.py -q

# OpenBao init (one-time)
docker compose exec openbao bao operator init     # save keys + root token
docker compose exec openbao bao operator unseal   # 3× different keys
```

Migrations run automatically on api startup (entrypoint `migrate --noinput`).

### Management commands (services/api)
`run_stream_processor`, `run_config_manager`, `run_alert_engine`, `run_security_engine`,
`run_cve_engine`, `run_lifecycle_engine`, `run_discovery`, `run_check_engine`,
`run_reachability_monitor`, `run_scheduler` (authoritative periodic scheduler),
`collect_arp_mac --all`, `update_mac_vendors`, `reset_test_data` (dev: clear app data, keep auth users),
seeders: `seed_alert_rules`, `seed_log_filters`, `seed_compliance_templates`.

## Django Apps (services/api/apps/)

core (base models, health, system settings) · devices (Device, Site, DeviceGroup, TopologyLink,
DiscoveryJob, DiscoveredDevice) · credentials (CredentialProfile; secrets in OpenBao) · telemetry
(TelemetryConfig, MonitoredInterface) · compliance (CompliancePolicy/Rule, ComplianceResult, +engine,
templates, overrides) · alerts (AlertRule, AlertEvent, AlertChannel) · cve · lifecycle · security
(DeviceRiskScore) · collectors · configbackup (ConfigBackupSettings, DeviceConfig) · integrations
(NetBox/DNA import; EmailSettings — SMTP for alert email, provider presets, password in OpenBao at
netpulse/integrations/smtp; GET/PUT /api/integrations/email/ + /test/; UnifiController — multi-controller
UniFi device import; local controller API credentials come from a linked CredentialProfile
(credential_profile FK; HTTPS preferred, SSH fallback — see get_controller_credentials), CRUD + /test//sync/
+ sync-all under /api/integrations/unifi/, 6h scheduler sync via UNIFI_SYNC_INTERVAL_S; AP + console
telemetry every UNIFI_TELEMETRY_INTERVAL_S (5m) → UnifiApStatus/UnifiConsoleStatus + InfluxDB
(unifi_ap_radio/unifi_ap_health, unifi_controller_health/unifi_wan); /api/wireless/{summary,aps}/,
/api/devices/{id}/{unifi-ap,unifi-console}/;
UnifiCloudAccount — UniFi Site Manager (api.ui.com) single-API-key auto-discovery of all controllers,
key in OpenBao at netpulse/integrations/unifi/cloud, GET/PUT + cloud/test + cloud/discover) · logs
(OpenSearch + LogFilter regex suppress/highlight/tag) · tls (SSL/CA mgmt) ·
checks (ServiceCheck, CheckResult; http/https/tcp/icmp/dns/tls/smtp/ssh_banner) · alerting (Team,
EscalationPolicy, AlertRoute — Stage 1: route matching + email) · sso (SSOProvider; Google OAuth2
Stage 1) · arp_mac (ARPEntry, MACEntry, MACVendor — SSH collection + OUI lookup) · mibs ·
**circuits** (WanCircuit — WAN circuit inventory + InfluxDB utilization + util/contract alerts;
`/api/circuits/`) · **chatops** (ChatOps Phase 1+2 — persistence/config + identity/RBAC/audit) ·
agents (Agent server monitoring) · frameworks (regulatory compliance) · reports · backup. Note:
**ManualTopologyLink** (manual topology links, `/api/topology/manual-links/`) and
**DeviceComplianceScore** (persisted weighted compliance score) live in the **devices**/**compliance**
apps respectively.

## Scheduler

ONE scheduler: the `run_scheduler` management-command loop (compose `scheduler` service, mounts
openbao-data:ro). Celery/django-celery-beat are in requirements but UNUSED — do NOT add a second
scheduler; add periodic work to run_scheduler. Startup one-shots (idempotent): seed alert rules,
unseal OpenBao, load OUI registry if empty. Periodic (tick 300s): alert purge (daily), ARP/MAC
collection (6h), MAC-vendor OUI refresh (weekly), hostname verification (24h,
`HOSTNAME_CHECK_INTERVAL_S`), UniFi controller sync (6h, `UNIFI_SYNC_INTERVAL_S`),
**daily compliance run** (hour-gated at `COMPLIANCE_RUN_HOUR`=03:00, same-day deduped),
**environment/PoE poll** (5m, `ENVIRONMENT_POLL_INTERVAL_S` — AOS-CX env+PoE → InfluxDB + PoE alert),
**WAN circuit checks** (15m, `CIRCUIT_CHECK_INTERVAL_S` — utilization + contract-expiry alerts),
plus the existing scheduled-reports + platform-backup tasks (both hour-gated + same-day deduped);
recurring tasks first fire one interval after start.

**Hostname verification** (`apps/devices/hostname_check.py`): re-checks active devices' hostnames via
SNMP sysName (1.3.6.1.2.1.1.5.0) then DNS reverse lookup; on a change it updates the device, raises an
INFO alert ("Device hostname changed", never auto-resolved), and re-applies hostname rules
(role/site). Every check stamps `Device.hostname_verified_at`. Also runs during device enrichment
(re-run enrichment / discovery approval) and on demand via `POST /api/devices/{id}/check-hostname/`
(returns `{hostname_changed, old_hostname, new_hostname}`). Interval via `HOSTNAME_CHECK_INTERVAL_S`
(default 86400s).

## Selected API Endpoints

`/api/health/` · `/api/health/infrastructure/` · `/api/auth/token/[refresh/]` · `/api/sso/providers/`
· `/auth/complete/{backend}/` · `/api/users/[me/]` · `/api/devices/` (+ `/topology/`,
`/test-connection/`, `/discovery/{jobs,discovered}/`, `/ping-summary/`) ·
`/api/devices/{id}/` (+ `/metrics/`, `/poll-now/`, `/interfaces/[discover/|alert-config/]`,
`/topology/discover/`, `/collection-status/`, `/reachability/`, `/arp/`, `/mac/`, `/arp-mac/collect/`)
· `/api/credentials/[:id/test/]` · `/api/sites/` · `/api/alerts/` · `/api/logs/[filters/]` ·
`/api/checks/[summary/|:id/{run-now,results}/]` · `/api/alerting/{teams,policies,routes,notifications}/`
· `/api/cve/` · `/api/lifecycle/` · `/api/network/{search,mac-vendor}/` ·
`/api/users/me/[preferences/]` (incl. `temperature_unit`) ·
`/api/circuits/[:id/utilization/]` · `/api/topology/manual-links/` ·
`/api/compliance/{run-all/[status/],run/:device_id/}` ·
`/api/alerts/events/{bulk-acknowledge,bulk-resolve,summary}/` ·
`/api/flows/resolve/[clear-cache/]` · `/api/devices/{id}/compliance/` (weighted score) ·
WS: `/ws/{telemetry,alerts,devices}/`. SSL/CA mgmt under `/api/settings/system/ssl/*`.

## Platform Support & Quirks (1–2 lines each; full guides in docs/platforms/)

Supported: ios_xe, ios (verified on real C8000V), ios_xr, nxos, junos, eos, fortios, panos, sonicwall,
aos_cx, aruba, sonicwall, plus aruba/aos.

- **Cisco IOS-XE/gNMI**: counters arrive `<IfName>/<leaf>`; memory/cpu Processor leaves mapped (see
  docs). gNMI dial-out on 57400; adaptive polling suppresses redundant SNMP while gNMI streams.
- **FortiOS**: no gNMI → SNMP (enterprise OID 12356) + syslog + NetFlow. Config = `show
  full-configuration` (strip `#config-version`/header lines before hashing). Paging-disable SSH events
  (`cfgpath=system.console`) are benign — normalizer tags `fortios_benign=true`. SNMP needs a valid
  license (unlicensed VMs emit `Secure Module Access Violation`, tagged `fortios_license_warning`).
- **SonicWall (SonicOS)**: REST preferred (RFC-7616 Digest SHA-256; v8=port 443, v7=port 4444; set
  `session.trust_env=False` + pass `verify=` per call). v7 config backup REQUIRES built-in `admin`
  (user accounts get 401). SSH ARP needs password sent TWICE + `no cli pager session` before `show arp
  caches` (paramiko direct — no Netmiko driver). SNMP CPU/mem OID subtree moved: poll BOTH
  `8741.1.3.1.x` (v8, % direct) and `8741.1.3.2.x` (v7, KB). No temp/fan/PSU via SNMP/REST.
- **AOS-CX (HPE 6100)**: SNMP-only on 6100 (REST 400/401). sysDescr `HPE ANW {model} {fw}`, enterprise
  47196. CPU=hrProcessorLoad WALK at vendor index (not .1), mem=hrStorage idx 1, temp=ENTITY-SENSOR,
  fan/PSU presence via entPhysicalClass. SNMPv3 "Wrong PDU digest" = WRONG key in OpenBao, not pysnmp.
  Aruba Central keepalive logs (`hpe-restd`) are normal noise. Central-managed config push: `aruba-central
  disable` → `sleep(2)` → push → re-enable (try/finally). gNMI on 8443 (OpenConfig) — planned.
  **REST API** (preferred enrichment when reachable; SNMP fallback) verified on FL.10.13 firmware
  (`wco2-mdf-crt-01` 10.150.0.15). LLDP on FL.10.13 uses the **per-interface** API, not
  `/lldp_neighbors_info`: `GET /system/interfaces/{port}` → `lldp_neighbors: {key: uri}`, then
  `GET /system/interfaces/{port}/lldp_neighbors/{key}` → `neighbor_info` (`chassis_id`,
  `chassis_name`, `chassis_capability_available` as comma string, `chassis_description`,
  `mgmt_ip_list`, `port_description`, `port_id_subtype`, `vlan_id_list`, `vlan_name_list`).
  Capabilities `"Bridge, Router"` → split/strip/lowercase → `["bridge","router"]`; mgmt IP =
  first entry of comma-separated `mgmt_ip_list`. Confirmed working: `GET /system?depth=1`. Next
  session: migrate interfaces/ARP/environment/VLANs/PoE off SNMP to REST (see Pending).
- **Discovery**: 4-tier (passive/topology-walk/active-scan/import); all land PENDING, never
  auto-activate. Default `ping_snmp` (production-safe). ⚠️ nmap Active Scan tripped a firewall block in
  the wco2 lab — reserve for labs. OT/ICS WARNING: never auto-probe industrial subnets (excluded_subnets).

## Docker NAT (Required)

Containers must MASQUERADE-NAT to the host IP for SNMP/SSH to devices that filter by source IP.
Applied by `setup.sh`/`update.sh` (shared logic in `scripts/nat.sh`, idempotent) on subnet
`172.18.0.0/16` (network `netpulse_netpulse-net`). If SNMP/SSH breaks after reboot:
`sudo ./netpulse.sh fix-nat`. Persisted via netfilter-persistent. The api-container health check WARNs
(no host iptables access) — apply on host.

## Known Lab Devices

> 🔒 Credentials are NOT in this repo — they live in OpenBao (Settings → Credentials) and gitignored
> `LOCAL_NOTES.md`. Never add passwords/keys here.

Local (192.168.98.x): router2 .152 ios_xe · router1.dnstest.local .100 ios · fortinet1 .155 fortios ·
soniclab .160 sonicwall (NSv XS, SonicOSX 8.2.1, v8/443).
Remote (host `azadmin@wco2lnxnetmon01`): wco2-idf5-asw-01 10.150.0.21 aos_cx (HPE 6100, verified) ·
wco2-idf6-asw-01 10.150.0.25 aos_cx · wco2-mdf-fw-01 10.16.128.129 sonicwall (TZ 670, SonicOS 7.3.2,
v7/4444, config backup needs built-in admin). AOS-CX SNMPv3 user `fpsrw` (authPriv SHA/AES).

## Pinned Decisions

- **Monorepo + multiple compose files** (decided 2026-06-03): one repo; `docker-compose.yml` = full
  stack, `docker-compose.collector.yml` = future collector; `setup.sh` asks deployment role.
- **ALLOW_CONFIG_PUSH=false by default** (read-only monitoring). Push/remediation endpoints return 403
  unless true; frontend reads it from `/api/settings/system/`; every push ATTEMPT is audit-logged.
- **Adaptive polling**: ingest-grpc stamps Valkey `gnmi:last_seen:{device_id}` (numeric id, TTL 180s);
  ingest-snmp skips the whole device poll while gNMI active (<120s); auto-resumes on stall. Disable via
  `ADAPTIVE_POLLING=false`. Status via `/collection-status/` (snmp.suppressed).
- **SNMP defaults**: SNMPv3 authPriv generated by default; SNMPv2c shows a plaintext warning; config
  preview shows write-only key placeholders (real keys fetched from OpenBao only at push time).
- **systemd boot service**: `netpulse.service` (Requires docker.service) auto-starts the stack.

## Known Issues / Gotchas

- Backend code is baked into the image (`COPY . .`) — **host edits don't run until rebuild** (`./netpulse.sh
  rebuild-api`).
- requests env-merge silently overrides per-request `verify=` with `REQUESTS_CA_BUNDLE` — SonicWall
  client sets `session.trust_env=False` AND passes `verify=` every call.
- OpenBao token resolution is lazy/self-healing in ingest-snmp (fixes "secrets empty after restart" race).
- Reachability liveness probes TCP/22 then TCP/443 fallback (firewalls blocking SSH still register live).
- Test counts (reference): services/api ~996, ingest-snmp ~58, ingest-grpc ~32.

## Security Rules (NEVER violate)

1. Never store plaintext credentials anywhere. 2. Use OpenBao `vault_path` reference in PostgreSQL.
3. Never return credential values in API responses. 4. Show "🔒 Stored securely in OpenBao" in
credential UI. 5. Scrub credentials from all logs. 6. mTLS for internal service comms. 7. TLS 1.3 min
for external. 8. Zero secrets in code or env vars in production.

Implemented: H1 auth rate limiting (DRF ScopedRateThrottle, keyed per client IP via X-Forwarded-For);
HTTPS enforced (nginx redirects :80→:443); OpenBao persistent secrets; OT/ICS exclusion in discovery;
ASCII config sanitization before push; Dependabot weekly.

## RBAC Roles (seeded)

Admin (full) · Engineer (read/write devices/configs/alerts) · Viewer (read-only) · API (service
account). Admin-only `/api/users/` CRUD with delete/demote guards (no self-delete, no removing the last
admin). JWT carries user/role (+ tenant when multi-tenancy lands).

## SSO (Stage 1 in progress)

App `apps/sso` (SSOProvider; `client_secret` in OpenBao at `secret/sso/{id}/credentials`, NOT the DB).
Built on social-auth-app-django; thin custom backend overrides `get_setting()` to read client_id from
DB + secret from OpenBao at request time. Pipeline enforces `allowed_domains`, assigns `default_role`
(viewer), mints the SAME JWT as local auth. Local admin login ALWAYS available
(`SSO_ALLOW_LOCAL_LOGIN=true`). Stage 1 = Google OAuth2 backend; Azure AD/Okta (2), SAML (3), LDAP (4),
login buttons + settings UI (5) are planned.

## spane Agent (server monitoring)

Lightweight Go agent for Linux/Windows server monitoring (`agent/`). Secure by
design: mTLS outbound only (443), no inbound ports, low-privilege user, unique
OpenBao-PKI cert per agent, single static binary (Linux core is stdlib-only).

- **Built (this session):** `apps/agents` backend (Agent, AgentEnrollmentToken,
  ServerRole + 7 built-in role profiles seeded, AgentRoleStatus) · APIs under
  `/api/agents/`: `enroll/` (public, token-authed → OpenBao-PKI-signed cert +
  auto-created Device), `{id}/metrics/` + `{id}/role-checks/` (client-cert authed
  via the `X-Agent-Cert-Serial` header the proxy sets), `{id}/roles/`, agent
  list/revoke, `tokens/` CRUD, `roles/` CRUD, `download/` · metrics → InfluxDB
  reusing cpu/memory/disk/interface measurements · Go agent source (Linux /proc
  collectors + Windows WMI/x-sys collectors, role port/service checks, mTLS
  transport, enrollment, systemd + Windows-service installers, `build.sh`,
  `build-agent.yml` CI) · frontend Settings → Agents (tokens + agents + role
  profiles).
- **Enrollment flow:** admin generates a token in the UI → one-line installer
  (`curl …/agent/install | sudo bash -s -- --token …` / `install.ps1`) → agent
  generates a keypair + CSR → server signs via OpenBao PKI → agent appears in
  inventory.
- **Cert issuance** is behind a mockable abstraction (`apps/agents/pki.py`).
- **OpenBao PKI is set up automatically** by `manage.py setup_agent_pki`
  (idempotent, run from entrypoint.sh): creates the `pki` mount, the "spane
  agent ca" EC P-384 root, the `agent` signing role (server-authoritative
  CN/SANs via `use_csr_*=false`), and the `netpulse-agent-pki` policy. The CA
  PEM is served at `GET /api/agents/ca-certificate/` (public). Binaries are
  served at `/agent/{install,download/<platform>}` from `settings.AGENT_DIR`.
- **nginx mTLS termination — BUILT (verified end-to-end).** nginx requests a
  client cert at the TLS handshake (`ssl_verify_client optional` against the
  agent CA; TLS 1.3 forbids per-location renegotiation), enforces it on the
  metrics/role-checks locations (403 without a CA-verified cert), and forwards
  `X-Agent-Verified`/`X-Agent-Cert-Serial`/`-Subject`; the generic `/api/`
  location strips those headers so they can't be spoofed. `AgentCertAuthentication`
  resolves the agent by normalized serial (nginx uppercase-no-colon vs OpenBao
  colon-lower). The CA PEM is published by `setup_agent_pki` to
  `settings.AGENT_CA_FILE` on the shared ssl-certs volume; the frontend
  entrypoint waits for it (placeholder fallback so nginx always starts).
- **Infra follow-up (NOT built):** Windows Phase-2 polish (event-log forwarding,
  custom PowerShell role checks). The Go binaries are built by CI, not in-repo.

## Pending (next session)

**Short list (this session's open items):** GitHub Releases for agent binaries (download views currently
redirect, falling back to the `agent/dist/` mount) · NetBox import preview UI polish · AOS-CX REST API
migration · Servers page polish · agent process monitoring · agent log forwarding · `install.ps1` endpoint
for Windows (`GET /agent/install.ps1` not yet routed) · marketing website (post v1.0).

### Agent download/install — DONE (one-liner verified end-to-end)
`GET /agent/install` (→ `scripts/install.sh`) and `GET /agent/download/<platform>`
(→ CI binaries) are served by Django (`apps/agents/download_views.py`) and
**proxied by nginx** (`location /agent/`); the repo `agent/` dir is bind-mounted
at `/agent` (`AGENT_DIR`) on the api service. Verified: the install script +
linux-amd64/linux-arm64/windows-amd64 downloads all return 200 through public
HTTPS. **Remaining agent gaps (genuinely pending):**
1. **`GET /agent/install.ps1` is NOT routed yet** — the Windows enrollment helper
   references `install.ps1` but only `agent/install` + `agent/download/<platform>`
   are wired in `config/urls.py`. Add a view serving `scripts/install.ps1` as
   `text/plain`, plus an nginx note (the `/agent/` block already covers it).
2. **Binaries are CI artifacts, not committed** — `build-agent.yml` produces the
   `agent-binaries` artifact (gitignored `agent/dist/`); it does NOT auto-update
   the served files. After a workflow run, refresh them with
   `gh run download --name agent-binaries --dir agent/dist` (or publish to a
   GitHub Release). The mounted `agent/dist/` must be repopulated to ship new
   agent behaviour (e.g. role auto-enable).
3. **Agent Phase-2 (NOT built):** process monitoring, log forwarding, Windows
   Event Log collection, custom PowerShell role checks (see the spane Agent
   section's "Infra follow-up").

### Collector (ingest) service builds — verify next session
All six ingesters are built (ingest-{snmp,syslog,flow,grpc,otlp,api-poller}).
Next session, verify each is running, healthy, and actually processing data in
both labs: `docker compose ps | grep ingest`; exercise each path end-to-end
(SNMP trap, syslog, NetFlow, gNMI stream, OTLP metric, REST poll); confirm the
NATS → stream-processor → InfluxDB/PostgreSQL/OpenSearch flow; scan
`docker compose logs ingest-*` for running-but-idle services; then document
which are production-ready vs placeholder.

## Planned Features (NOT built — specs in docs/)

ChatOps · network topology auto-map + utilization overlay + circuit capacity overrides · availability/
uptime + WAN SLA reporting + maintenance windows · business-service health · TV/NOC display mode ·
firewall traffic log analytics ·
gNMI capability discovery + platform profiles (dial-IN needed) · support-bundle generator · config
git-sync (Tier 2) · multi-tenancy (Tenant/TenantUser, TenantViewSet) · ChatOps user-identity profile
fields · NetBox import · CVE applicability engine · lifecycle/EOL UI · SMS/PagerDuty alerts · alert
routing Stage 2+ (on-call, ack/snooze, Slack/PagerDuty/webhook channels) · Aruba Central integration ·
AOS-CX/Aruba telemetry config templates + enrichment. Do NOT document these as current.

## Pre-Release / Production Checklist

Before public v1.0:
- [ ] Remove/restrict `apps/credentials/management/commands/show_credentials.py` (dumps credential info).
- [ ] Remove `scripts/check_keys.py` if present.
- [ ] Audit all management commands for security-sensitive output.
- [ ] Review DEBUG, SECRET_KEY rotation docs, ALLOWED_HOSTS.
- [ ] SSL/TLS cert setup docs; remove any hardcoded test creds from docs/examples.

**Pre-production security audit** (run ONLY when explicitly requested — it's a gate, not a dev task):
- Automated: `pip-audit`/`safety` (Python deps), `bandit`/`semgrep` (SAST), `npm audit` (frontend),
  `trivy`/`docker scout` (images), `gitleaks`/`trufflehog` (secrets).
- Manual review across: authn/authz, input validation (SQLi/cmd-injection/path-traversal/CIDR),
  secrets mgmt, API security (rate limits/CORS/CSRF/error verbosity), network security, data security,
  SSH/device access, Docker hardening, dependency CVEs/licenses.
- Output `SECURITY-REPORT.md` (Critical/High/Medium/Low + remediation). Do NOT deploy until all
  Critical + High are resolved or accepted with justification.

Collector deployment (post v1.0): transport substrate is VALIDATED and the central side is BUILT (see
"Remote Collector Subsystem" status above). Remaining: `docker-compose.collector.yml`, role selection
in setup.sh, the `services/collector` agent (mTLS/buffer/replay + broker client), the broker
identity-from-transport end-to-end proof, multi-collector test.

## Marketing Website (post v1.0)

- [ ] Create marketing/docs website (separate from the ReadTheDocs technical docs).

  Content:
  - Hero section with screenshots/demo
  - Feature highlights with visuals
  - Quick install one-liner
  - Platform support matrix
  - Architecture overview diagram
  - Getting started guide
  - Screenshots of key UI pages: Dashboard · Device detail (telemetry) · Flow analytics + Sankey ·
    CVE intelligence · Config compliance · Config diff viewer

  Tech options:
  - Static site: Astro, Hugo, or Jekyll (can live in a `/website` folder in the monorepo)
  - Hosted: GitHub Pages (free, auto-deploy)
  - Domain: whatever name we settle on

  GitHub Pages setup:
  - Branch: `gh-pages`
  - Auto-deploy via GitHub Actions on push

  Screenshots:
  - Take screenshots of the running local lab
  - Store in `/website/static/screenshots/`
  - Use the dark-mode UI for marketing appeal
