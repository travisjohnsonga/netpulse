# CLAUDE.md

Guidance for Claude Code working in this repo. These instructions override default behavior.

> **Detail lives in `docs/`.** This file is a quick-reference. For full detail see
> `docs/ARCHITECTURE.md`, `docs/setup/{deployment,nat}.md`, `docs/platforms/{fortios,sonicwall,aos_cx}.md`,
> and the per-feature docs; lab-specific/credential notes live in gitignored `LOCAL_NOTES.md`. Many large feature
> designs (ChatOps, topology, availability/SLA, business-service health, TV/NOC mode, distributed
> pollers, firewall analytics, gNMI capability discovery/profiles, support bundle, multi-tenancy,
> config git-sync) are **planned, not built** — see "Planned Features" below and `docs/` for specs.

## Project Overview

NetPulse — push-first, open-source network intelligence platform: gRPC/gNMI streaming telemetry,
config compliance, CVE intel, lifecycle, log anomaly detection, unified risk scoring.

Stack: Python 3.13, Django 6.0 + DRF + Channels (backend), React + TypeScript + Vite + Tailwind +
ECharts + Cytoscape.js + D3 + React Query + Zustand (frontend), Docker Compose (on-prem), Helm (cloud).
PostgreSQL 17, InfluxDB (time-series), OpenSearch (logs), Valkey (cache/WS broker), NATS+JetStream
(bus), OpenBao (secrets, Vault-compatible). Auth: JWT (SimpleJWT) + SSO (social-auth) minting same JWT.

## Current State (June 2026)

- Tests: ~1275 passing (services/api, in-memory SQLite). Services: 24/24 running. Python 3.13,
  Django 6.0. Frontend: React + Vite.

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

**Recently completed:** default admin password `NetPulse1!` + forced change on first login ·
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
a factory reset (re-unseal/re-init).

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
Stage 1) · arp_mac (ARPEntry, MACEntry, MACVendor — SSH collection + OUI lookup) · mibs.

## Scheduler

ONE scheduler: the `run_scheduler` management-command loop (compose `scheduler` service, mounts
openbao-data:ro). Celery/django-celery-beat are in requirements but UNUSED — do NOT add a second
scheduler; add periodic work to run_scheduler. Startup one-shots (idempotent): seed alert rules,
unseal OpenBao, load OUI registry if empty. Periodic (tick 300s): alert purge (daily), ARP/MAC
collection (6h), MAC-vendor OUI refresh (weekly), hostname verification (24h,
`HOSTNAME_CHECK_INTERVAL_S`), UniFi controller sync (6h, `UNIFI_SYNC_INTERVAL_S`); recurring tasks
first fire one interval after start.

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

## NetPulse Agent (server monitoring)

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
- **Infra follow-up (NOT built):** nginx mTLS termination passing the verified
  client-cert serial to Django; OpenBao PKI engine enablement
  (`bao secrets enable pki` + `pki/roles/agent`); CI-published binary serving at
  `/agent/download/<platform>`; Windows Phase-2 polish (event-log forwarding,
  custom PowerShell role checks). The Go binaries are built by CI, not in-repo.

## Planned Features (NOT built — specs in docs/)

ChatOps · network topology auto-map + utilization overlay + circuit capacity overrides · availability/
uptime + WAN SLA reporting + maintenance windows · business-service health · TV/NOC display mode ·
distributed remote pollers · NetPulse collector agent (mTLS forward) · firewall traffic log analytics ·
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

Collector deployment (post v1.0): `docker-compose.collector.yml`, role selection in setup.sh,
collector-agent forwarding service (mTLS/buffer/replay), multi-collector test, docs.

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
