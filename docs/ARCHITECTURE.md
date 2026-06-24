# spane Architecture & Design Document

> This document captures the full architecture, design decisions, and requirements
> defined during the initial project design session. It serves as the authoritative
> reference for all development decisions.

---

## Project Vision

A push-first, open source network intelligence platform that solves real problems
ignored by traditional monitoring tools. Built for modern infrastructure, vendor-agnostic,
deployable on-prem via Docker Compose or cloud-hosted via Kubernetes.

**Key Differentiator:** Most platforms poll devices on a schedule. spane is built
around streaming telemetry (gRPC/gNMI, InfluxDB line protocol, OTLP) with polling
as a fallback for legacy devices only. This is confirmed working against a real
device (Cisco C8000V): SNMP polling and streaming telemetry run side by side, with
the stream-processor reconciling both. **Cisco MDT (Model-Driven Telemetry)** is
supported alongside standard gNMI, covering IOS-XE/XR devices that use Cisco's
gRPC dialout rather than OpenConfig dialout.

---

## Core Capabilities

### 1. Push-First Telemetry Ingest
- **gRPC / gNMI** — high-throughput streaming telemetry (Cisco IOS-XR, Juniper, Arista)
- **InfluxDB line protocol** — direct metric ingestion
- **OpenTelemetry (OTLP)** — vendor-neutral metrics, traces, logs
- **NetFlow / sFlow** — flow collection with inter-device path latency correlation
- **SNMP** — polling fallback for legacy devices that don't support push
- **Syslog** — normalized, enriched log ingestion

**Why push over poll:**

| | Polling | Push/Streaming |
|---|---|---|
| Latency | 30s–5min gaps | Sub-second |
| Scalability | Monitoring system is bottleneck | Devices distribute load |
| Accuracy | Misses spikes between polls | Captures every event |
| Device load | Unpredictable (poll storms) | Predictable |

---

### 2. Bandwidth & Capacity Planning
- 95th percentile trending per circuit (standard billing metric)
- Year-over-year growth rate calculation with seasonal adjustment
- Capacity threshold forecasting ("this circuit hits 80% in 7 months")
- Budget planning reports exportable to PDF/CSV
- Circuit metadata — provider, cost, contract renewal date, committed rate
- Recommended upgrade trigger dates

**Key output:** A budget planning report a network manager can hand to finance —
not a dashboard, an actual document with projected spend.

---

### 3. Configuration Intelligence
- Jinja2 template-based compliance engine with role-based templates and variable overrides
- Cross-device consistency validation by role, site, platform
- Config drift detection — MISSING, EXTRA, DRIFT classifications
- Remediation config snippets (ready to push)
- Per-device compliance score
- Operational command runner across device fleets
  - TextFSM/TTP parsing of CLI output into structured data
  - Cross-device diff — run same command on 50 switches, show differences
  - Output stored in PostgreSQL — queryable, historical, comparable
  - Scheduled audits with drift alerting
- Full config versioning and diff history
- Protocols: NETCONF/YANG, gNMI, SSH+TextFSM, REST API, SNMP (last resort)

---

### 4. Log Intelligence

#### Noise-Aware Anomaly Detection
- **Phase 1 — Baseline:** Learn normal frequency and vocabulary per device+pattern
- **Phase 2 — Period Analysis:** For a specified period (e.g. "last 3 days"):
  - Suppress known-normal messages at normal frequency
  - Flag: NEW patterns, FREQUENCY spikes, CORRELATED bursts, SEQUENCE anomalies, SILENT devices
  - Rank by likely significance, not volume
- **Phase 3 — Enrichment:** Cross-reference with interface telemetry and config changes

#### Group Trend Analysis (Vendor Bug Detection)
Device dimensions for grouping: role, site, platform, OS version, hardware, vendor, custom tags

Pattern correlation engine:
- Fingerprint message patterns (strip variable data, extract stable signature)
- Track occurrence across device dimensions
- Classification: VENDOR BUG CANDIDATE, ENVIRONMENTAL, CONFIG DRIFT, ISOLATED

**Vendor Intelligence:** Cross-reference detected patterns against known advisories
(Cisco PSIRT API, Juniper advisories, community-maintained YAML files)

**Key view:** Log pattern heatmap across the fleet — not a table of messages

---

### 5. Security Authentication Reporting

Detection scenarios:
- **Brute Force:** failures > X in Y minutes from same source → did it succeed?
- **Distributed Attack:** same source across > N devices in Y minutes
- **Success After Failures:** any success preceded by > X failures (always high severity)
- **Slow Burn:** same source accumulates failures over days/weeks
- **Off-Hours Access:** successful login outside defined business hours
- **New Source:** successful login from IP not seen in last 90 days
- **Username Enumeration:** many different usernames from same source

Scheduled security reports (daily/weekly) plus on-demand query.
Normalized auth events across all vendors: Cisco IOS, Juniper, Arista, Fortinet,
Palo Alto, Linux auth.log, TACACS+/RADIUS.

---

### 6. Flow Analytics
- NetFlow v5/v9, IPFIX, sFlow collection
- **Inter-device transit latency correlation** — measure hop-by-hop WAN latency from flow data
  - Match flows by 5-tuple across adjacent devices
  - Delta timestamps = transit latency per hop
  - Store per-link latency distribution in InfluxDB
- Application latency scoring
- Baseline + deviation alerting
- QoS validation hop-to-hop
- Asymmetric path detection

---

### 7. Lifecycle Management
- Hardware and software EOL tracking
- Automated ingestion: Cisco EoX API, Juniper advisories, Arista scraper, community YAML
- Status tracking: Active, EOS, EOM, PAST EOM, PAST EOSL
- Proactive alerts: 365/180/90 days before EOSL
- Refresh planning with cost estimates (1/2/3 year forecasts)
- Budget integration — ties to bandwidth planning reports

---

### 8. CVE Intelligence & Applicability Engine

**Key differentiator:** Not just version matching — config-aware applicability.

Applicability conditions example:
- CVE affects HTTP server on IOS-XE
- Check if `ip http server` is present in running config
- Check if ACL restricts HTTP access
- Result: VULNERABLE / MITIGATED / NOT_APPLICABLE / PATCHED / UNVERIFIED

Config check methods:
- `config_search` — pattern in running config
- `config_value` — specific value comparison
- `telemetry_check` — cross-reference live telemetry
- `feature_enabled` — structured data from NETCONF/gNMI
- `acl_present` — ACL protection check

CVE sources: NVD API, Cisco PSIRT openVuln API, Juniper/Arista advisories,
CISA KEV (Known Exploited Vulnerabilities), community enrichment

Compliance exports: PDF, POAM format, remediation workplan, delta report,
"prove we're not affected" audit report.

---

### 9. Unified Risk Score

Per-device risk score synthesizing:
- EOL/EOSL status
- CVE exposure and severity
- Security authentication events
- Configuration drift
- Bandwidth headroom

Executive summary view across entire estate.

---

### 10. spane Remote Collector (On-Prem → Central)

A remote collector polls local devices and forwards their telemetry to a central
spane over a **single outbound mTLS connection** (443/8443) — no inbound
firewall rules on the customer network. This section reflects the design as
**built and validated tonight**; maturity is marked per piece. Detailed proofs
live in `scripts/t0`–`scripts/t3` (NATS substrate harnesses) and the
`apps/collectors` code; the production gates are in
`docs/collector-production-gates.md` and the operator steps in
`docs/collectors/runbook.md`.

Maturity legend: **[VALIDATED]** proven end-to-end · **[BUILT]** committed, not
yet proven end-to-end · **[PLANNED]** not built.

#### Transport substrate — NATS leaf + edge JetStream  [VALIDATED]

Telemetry does **not** ride a bare leaf-forward. The edge runs its own JetStream
stream that captures local telemetry; the central hub *sources* from that edge
stream and resumes by acked sequence after any disconnect.

> **Why a buffer in the edge stream, not a straight forward:** a plain leaf
> forward is fire-and-forget — anything published while the link is down is lost.
> Putting the durable buffer in the **edge JetStream stream** means a disconnect
> just stalls the hub's source consumer; on reconnect it resumes from the last
> acked sequence with no loss and no duplicates. Proven in `scripts/t0`
> (cut → buffer → replay, zero loss/zero dup) and `scripts/t2` (the same across
> the inter-NATS hop).

The leaf is mTLS, TLS 1.3 minimum, `handshake_first: true` (TLS before any
plaintext INFO). The hub's leaf listener sets `advertise: <fixed-ingress>` +
`no_advertise: true` so a reconnecting collector always re-dials the published
ingress and never the hub's own address. `[VALIDATED]` (scripts/t0, scripts/t1).

#### Topology — a separate operator-mode collector-hub  [VALIDATED design]

Collectors terminate on a **dedicated operator-mode "collector-hub" NATS**, which
links to the existing internal NATS to hand off telemetry.

> **Why a separate hub:** NATS operator/JWT auth is **server-wide** and mutually
> exclusive with the internal bus's `authorization { user/password }`. The
> internal 4222 user/pass listener must stay **untouched** (in-cluster services
> depend on it), so the collector-facing operator mode cannot live on the same
> node. The internal NATS therefore **never learns collectors exist** — it dials
> *one* leaf bound to a single aggregate account and *sources one stream*; all
> per-collector accounts, ingress, `advertise`/`no_advertise`, and isolation live
> on the collector-hub. Proven in `scripts/t2` (a collector-account publish
> reaches the internal stream-processor consumer, surviving a cut with no
> loss/dup; the internal bus config is untouched).

#### Identity model — bus identity vs transport identity  [VALIDATED]

Two distinct identities ride the one leaf connection:

- **Bus identity = per-collector operator-signed account** (its `.creds`). Each
  collector is its own NATS account (isolation + tenant-ready). Accounts are
  signed by the operator **signing key**, never the operator **identity key**.
  > **Why the split:** the identity key is the root of the whole collector trust
  > hierarchy. It is kept **cold/offline**; only the signing key is online to mint
  > accounts. Rotating or revoking the signing key never requires touching the
  > cold identity key. (Finding from `scripts/t3`: minting must pass `-K <signing
  > key>` or nsc silently signs with the identity key.)
- **Transport identity = per-collector mTLS cert** from the OpenBao PKI
  intermediate (`pki_int`, role `collector`, client-auth only). The cert proves
  the *transport*; the account `.creds` prove the *bus*. Keeping them distinct
  means a stolen cert without creds (or vice-versa) is useless.

**Account resolver = `full` (NATS-based, local store).** Account JWTs are pushed
once and cached locally on the collector-hub.
> **Why:** a reconnecting collector resolves from the local store, so resolver
> availability is **out of the steady-state connect path** — a brief resolver
> outage only blocks a brand-new enrollment, never existing collectors. (Memory
> mode needs a reload to add accounts; URL mode puts an external server in the
> cold-lookup path — both rejected.)

Adding/revoking a collector is a runtime account JWT push (**no `nats.conf`
reload**). Rotating the operator signing key is the rare exception that needs a
connection-preserving SIGHUP — see the runbook. `[VALIDATED]` (scripts/t1
revoke-without-reload; scripts/t3 rotation both modes).

#### Device credentials — Option A: central OpenBao broker  [broker logic VALIDATED, identity-wiring BUILT]

A remote collector **never reads device credentials from OpenBao itself**. It
asks a central **secret-broker** over the authenticated leaf (NATS
request/reply); the broker returns the creds **RAM-only, short TTL**, never to
disk.

Confused-deputy defense (broker authorization logic — **[VALIDATED]** by
`tests/test_secret_broker.py`, 34 cases):
- **Identity is the authenticated transport account**, never a field in the
  message body. A body that names a different collector cannot widen access.
- **The allowed set is derived server-side** from the single authority
  `resolve.effective_collector(device)` — a collector may fetch a device's creds
  **iff** `effective_collector(device) == that collector`. `resolve.py` is reused,
  never reimplemented.
- **The request can only narrow** (pick a device/protocol). The broker *computes*
  the vault path for an owned device and reads only that — never a path the
  collector supplies — and validates it against a strict shape.
- **Least privilege:** the broker reads via an OpenBao AppRole scoped to
  `read` on `secret/data/netpulse/credentials/+` with **no list anywhere** —
  verified against the **live** OpenBao (read ✓, list 403, out-of-scope 403). A
  bug degrades to "fails to fetch," never "enumerates the vault." **[VALIDATED].**
- **Fail closed:** in production the broker refuses to start (and refuses to read)
  without its scoped AppRole. **[VALIDATED].**

> **Why A, not B or C:** **B — local vault sync** (replicate a credential subset
> to each edge) was rejected: it puts standing plaintext-capable secret material
> at every remote site and multiplies the blast radius of a stolen edge. **C —
> encrypted KV injection** (push encrypted creds down the config bundle) was
> rejected: the edge would need the decryption key resident, which is the same
> exposure as B with extra moving parts. Option A keeps **all** standing OpenBao
> read access on **one** central, audited, least-privilege broker; the edge holds
> only short-TTL RAM copies of the creds for devices it actually owns.

**Open item (the one thing not yet end-to-end): [BUILT]** — the NATS
cross-account service routing that conveys the caller's account to the broker is
committed but not yet proven over the real transport (account_token_position vs a
per-account import subject — routing still open). Until the A-can't-fetch-B proof
passes over the real leaf, this is a **blocking production gate** (see
`docs/collector-production-gates.md`).

#### Central-side plumbing  [BUILT]

Committed and unit-tested (`apps/collectors`), not yet exercised by a live agent:
- **Enrollment:** one-time enrollment token → exchanged once for an API key
  (bcrypt-hashed) + a NATS account + a best-effort per-collector PKI cert.
- **Config-DOWN:** a per-collector non-secret config bundle (devices as
  credential-path *references* only, checks, a sha256 revision) published to a
  per-collector JetStream KV bucket; ownership-aware republish on device/site
  reassignment.
- **Single assignment authority:** `resolve.effective_collector` (device.collector
  → site.default_collector → global `is_default` → `COLLECTOR_IP`) and its inverse
  `resolve.devices_for_collector`. **`Site.default_collector` is the sole
  site-level authority** — there is no separate site-assignment list to drift.

#### Not built yet  [PLANNED]

The on-edge **collector agent** (`services/collector`: the forwarding process,
local buffer/replay, broker client), `docker-compose.collector.yml`, and the
`setup.sh` role selection are **not built**. Do not treat the deployment snippet
in older drafts as runnable.

---

### 11. Single Sign-On (SSO)

Enterprise authentication via external identity providers. Local admin login is
**always** available as a fallback so a provider outage can't lock everyone out.

**Supported providers:**
- Google Workspace (OAuth2) — Stage 1
- Microsoft Azure AD (OAuth2 + tenant) — Stage 2
- Okta (OAuth2) — Stage 2
- GitHub (OAuth2)
- SAML 2.0 — planned (Stage 3)
- LDAP / Active Directory — planned (Stage 4)

**Features:**
- Domain restriction (e.g. only `@company.com` may sign in)
- Auto-assign a role to new SSO users (default: viewer)
- Sync name/email from the identity provider
- `client_secret` stored in OpenBao (never in PostgreSQL)
- Multiple providers configured simultaneously
- Optional default provider (auto-redirect from the login page)
- New-user signup control per provider

**Implementation:**
- Pipeline: `social-auth-app-django` (social-auth core)
- Provider config lives in the `SSOProvider` model; per-provider secrets in
  OpenBao KV-v2 at `secret/sso/{provider_id}/credentials`
- Tokens: SSO login mints the **same JWT** (DRF SimpleJWT) as local auth, so the
  frontend/API treat both identically
- Dynamic settings: a thin custom backend overrides `get_setting()` to read
  `client_id`/`client_secret` from the DB+OpenBao at request time (social-auth
  normally reads keys from Django settings)

---

## Deployment Modes

spane uses a monorepo with multiple Docker Compose profiles for different
deployment scenarios.

### Supported host OS

- Ubuntu 22.04 LTS (Jammy)
- Ubuntu 24.04 LTS (Noble) ← recommended

Other Linux distributions may work but are not tested or supported. The
one-line installer (`scripts/install.sh`) only provisions prerequisites via
`apt-get`.

### Mode 1: Full Stack (default)

Complete spane installation with all services including UI, API, and all
engines.

```bash
docker compose up -d
# or
./setup.sh → select "Full Stack"
```

- **Services:** all 24 services
- **Use case:** Primary spane server

### Mode 2: Collector Only  [PLANNED — substrate validated, agent not built]

Lightweight collector that forwards telemetry to a central spane server. No
UI, no DB, no processing engines. The transport substrate is **validated**
(`scripts/t0`–`scripts/t3`); the on-edge agent and packaging below are **not yet
built** — the compose file and `setup.sh` role do not exist.

```bash
docker compose -f docker-compose.collector.yml up -d   # [PLANNED] not built yet
# or
./setup.sh → select "Collector"                        # [PLANNED] not built yet
```

- **Services:** ingest-snmp, ingest-syslog, ingest-flow, ingest-grpc, the
  collector agent, and a **local edge NATS with a JetStream stream as the durable
  buffer** (NOT valkey — the buffer lives in the edge JetStream stream that the
  central hub sources from by acked sequence; see §10). The earlier
  "valkey (buffer only)" note was wrong and predates the validated design.
- **Use case:** Remote sites forwarding to central over one outbound mTLS leaf

### Mode 3: Cloud Hosted (future)

Central server receives from multiple collectors. Multi-tenant capable.

```bash
docker compose -f docker-compose.cloud.yml up -d
```

### Setup.sh role selection (future)

When collector mode is implemented, `setup.sh` will ask:

```
Select deployment role:
1) Full Stack — complete spane server
   (UI, API, all engines, local storage)
2) Collector — lightweight telemetry forwarder
   (no UI, forwards to central server)
3) Custom — choose individual components
```

### Shared code strategy

Monorepo approach — all services in one repo. The collector uses the same ingest
service images as the full stack. No sync issues.

Collector-specific config:

```
NETPULSE_MODE=collector
NETPULSE_SERVER=https://central.example.com
NETPULSE_API_KEY=collector-api-key
COLLECTOR_SITE=dc-west
```

### Collector architecture (future)

See the **spane Collector** section above. Implementation planned post v1.0
release.

---

## Technology Stack

All components are open source with permissive licenses. Zero licensing landmines.

| Layer | Technology | License | Notes |
|---|---|---|---|
| Time-series metrics | InfluxDB OSS | MIT | High-frequency writes |
| Primary database | PostgreSQL 17 + JSONB | PostgreSQL | Replaces MongoDB — SSPL concern |
| Search / logs / flows | OpenSearch | Apache 2.0 | Replaces Elasticsearch — SSPL concern |
| Cache + WebSocket broker | Valkey | BSD 3-Clause | Replaces Redis 7.4+ — license change |
| Message bus | NATS + JetStream | Apache 2.0 | Lighter than Kafka for on-prem |
| Secrets management | OpenBao | MPL 2.0 | HashiCorp Vault fork — BSL concern |
| API framework | Django 6.0 + DRF | BSD | Latest stable |
| WebSockets | Django Channels | BSD | |
| SSO / OAuth2 | social-auth-app-django | BSD | Google / Azure AD / Okta / GitHub |
| JWT validation | python-jose | MIT | SSO ID-token validation |
| Frontend | React | MIT | |
| Charting | Apache ECharts | Apache 2.0 | |
| Config templates | Jinja2 | BSD | |
| CLI parsing | TextFSM / ntc-templates | Apache 2.0 | show-command parsing |
| Stream processing | asyncio + nats-py | Apache 2.0 | NATS consumers (Django mgmt commands) |
| gRPC | grpcio | Apache 2.0 | |
| SNMP | pysnmp | BSD | |
| Async HTTP | aiohttp | Apache 2.0 | service-check HTTP/HTTPS probes |
| Async DNS | aiodns | MIT | service-check DNS probes |
| Async SMTP | aiosmtplib | MIT | service-check SMTP probes |
| ICMP | icmplib | Apache 2.0 | service-check ping (loss/RTT/jitter) |
| Device comms | Netmiko / ncclient | MIT / Apache 2.0 | |
| Python version | 3.13 | | Latest stable, Django 6.0 supported |
| Project license | Apache 2.0 | | Permissive + patent protection |

---

## Microservices

| Service | Description | Build Context |
|---|---|---|
| `ingest-grpc` | gRPC/gNMI stream receiver | services/ingest-grpc |
| `ingest-snmp` | SNMP poller (legacy fallback) | services/ingest-snmp |
| `ingest-syslog` | Syslog receiver and normalizer | services/ingest-syslog |
| `ingest-flow` | NetFlow/sFlow collector | services/ingest-flow |
| `ingest-otlp` | OpenTelemetry collector | services/ingest-otlp |
| `ingest-api-poller` | Cloud-API pollers (Meraki/Mist/UniFi) | services/ingest-api-poller |
| `stream-processor` | Real-time anomaly detection and correlation | services/api |
| `config-manager` | Config collection, compliance, diff engine | services/api |
| `alert-engine` | Rule evaluation and notification dispatch | services/api |
| `cve-engine` | CVE ingestion and applicability scoring | services/api |
| `lifecycle-engine` | EOL tracking and refresh planning | services/api |
| `security-engine` | Auth event analysis and attack detection | services/api |
| `api` | Django REST Framework API | services/api |
| `websocket` | Django Channels live updates | services/api |
| `frontend` | React SPA | services/frontend |
| `scheduler` | Authoritative periodic-task loop (`run_scheduler`): alert purge (daily), ARP/MAC collection (6h), MAC-vendor OUI refresh (weekly) + startup seeding. Celery is unused. | services/api |
| `check-engine` | Agentless service-check runner (HTTP/HTTPS/TCP) | services/api |
| `reachability-monitor` | TCP/22 device liveness + status transitions | services/api |
| `collector` | On-prem → cloud telemetry forwarder (planned) | services/collector |

---

## Security Architecture

### Principles
1. Never store plaintext credentials — anywhere, ever
2. Encrypt at rest AND in transit — always
3. Least privilege — services only access what they need
4. Audit everything — every credential access logged
5. Assume breach — design so stolen DB is useless without keys
6. Rotate easily — credential rotation should be frictionless
7. Zero secrets in code — no hardcoded anything, ever
8. Secrets never in logs — scrub before writing

### Authentication Methods
- **Local** — username/password → JWT (DRF SimpleJWT). Always available as a
  fallback; the first admin is created locally by `scripts/setup.sh`.
- **SSO** — OAuth2 / SAML via external IdP → the **same JWT** format as local
  auth (see Core Capability #11).
- **API** — Bearer JWT for service/integration accounts.
- **Service-to-service** — OpenBao AppRole per microservice (least privilege).

**SSO security:**
- `client_secret` stored in OpenBao only — never in PostgreSQL or logs.
- Domain allowlist (`allowed_domains`) enforced server-side in the auth pipeline.
- New SSO users default to the **viewer** role; an admin must explicitly elevate.
- HTTPS required for OAuth2 callbacks (already enforced by the frontend proxy).
- Redirect URIs validated to prevent open redirects.

### Authorization (RBAC)

Authorization is **capability-based and deny-by-default** — not a fixed
admin/engineer/viewer hierarchy. Access is expressed as a catalog of fine-grained
`domain:action` **capabilities** (e.g. `device:edit`, `config:push`,
`rbac:manage`); a **role** is a set of capabilities, and every API endpoint
declares the capability it requires. An endpoint that declares none is denied, so
a forgotten check fails closed rather than leaking access.

- The capability catalog and seeded roles live in code
  (`apps/core/capabilities.py`); the default DRF permission class is
  `DenyByDefault` (`apps/core/permissions.py`).
- Five **system roles** are seeded — `superadmin` (immutable), `admin`,
  `engineer`, `api`, `viewer` — and operators can define **custom roles** with
  any subset of capabilities, subject to an anti-escalation rule (you cannot
  grant a capability you do not hold).
- The legacy single `role` field is retained for back-compat, but authorization
  is resolved from the user's assigned RBAC role.

For the full security model see
[Security → Authorization (RBAC)](security/authorization.md); for the operator
guide to creating and assigning roles, see
[Admin → Access Roles](admin/access-roles.md).

### Credential Storage Pattern
```
PostgreSQL (devices table):
  credential_path: "secret/devices/{uuid}/ssh"  ← path reference only
  username: "monitor_user"                        ← username is OK
  # NO passwords, NO keys, NO secrets in database

OpenBao KV-v2:
  secret/devices/{uuid}/ssh:
    username: "monitor_user"
    password: "..."     ← encrypted by Vault Transit
    private_key: "..."  ← encrypted by Vault Transit
```

### Service Identity (Least Privilege)
Each microservice has its own AppRole with minimal Vault policy:
- `ingest-snmp` can only read `secret/data/devices/+/snmp`
- `config-manager` can only read `secret/data/devices/+/ssh`
- No service gets list access to all credentials at once

### Hardening (implemented)
- **SNMPv3 authPriv enforced by default** — config generation produces SNMPv3
  (auth + privacy) when the credential profile is v3; SNMPv2c falls back with a
  plaintext-security warning surfaced in the UI. Auth/priv keys are write-only
  (placeholders in generated config, real keys fetched from OpenBao on push).
- **Auth endpoint rate limiting** — JWT obtain/refresh endpoints are throttled
  (DRF ScopedRateThrottle, `AUTH_THROTTLE_RATE`, default 10/min) to blunt
  brute-force login attempts (security finding H1). Keyed per client IP:
  `NUM_PROXIES=1` + nginx `X-Forwarded-For` so it is enforced per client behind
  the frontend proxy rather than collapsing onto the shared nginx container IP.
- **ASCII sanitization before config push** — `sanitize_config_for_push()`
  maps non-ASCII (em dash, smart quotes, box-drawing) to ASCII and strips
  comment lines, preventing "% Invalid input" / config-injection surprises on
  IOS/IOS-XE when pasting or pushing.
- **Config push disabled by default** — `ALLOW_CONFIG_PUSH=false`; the platform
  is read-only until a network team explicitly enables pushes.
- **Dependabot** — automated weekly dependency updates (pip/npm/docker/actions),
  grouped (Django/React/Tailwind).
- **OT/ICS subnet exclusion** — discovery never probes excluded subnets.

---

## Network Architecture

### Container NAT

All spane container traffic is NAT'd to the host IP address using iptables
MASQUERADE.

**Rationale:**
- Network devices often restrict SNMP/SSH access by source IP.
- The Docker bridge subnet (172.x.x.x) is typically not in device ACLs.
- NAT ensures containers use the host IP, which *is* in device management ACLs.
- Prevents the Docker subnet from conflicting with existing network
  infrastructure.

**Implementation:**
- Applied automatically during `setup.sh` (shared logic in `scripts/nat.sh`),
  re-applied during `scripts/update.sh`, and re-runnable via
  `sudo ./netpulse.sh fix-nat`.
- Rule: `iptables -t nat -A POSTROUTING -s <subnet> ! -d <subnet> -j MASQUERADE`
  on the netpulse bridge subnet (default 172.18.0.0/16).
- Persisted across reboots via `netfilter-persistent` (or
  `/etc/iptables/rules.v4`).
- `run_health_checks` includes a "Docker NAT" check (warns from inside the
  container, where it has no host iptables access; verify/apply on the host).

---

## Data Architecture

### PostgreSQL (Primary Database)
- Device inventory (Device incl. reachability state: is_reachable, unreachable_since)
- Sites, device groups, TopologyLink (LLDP), discovery jobs/results
- CredentialProfile metadata (secrets live in OpenBao, never here)
- SiteCredential — maps a CredentialProfile to a Site (optionally per DeviceRole, with priority);
  devices added/discovered into a site auto-inherit the matching profile
- CVE (CVE, DeviceCVE), LifecycleMilestone, DeviceRiskScore
- Alert rules/events/channels, config backup settings + DeviceConfig metadata
- ServiceCheck + CheckResult (agentless monitoring)
- Integrations: NetBoxImport history, EmailSettings (SMTP), UnifiController / UnifiCloudAccount
  (UniFi controllers + Site Manager cloud account) — all secrets in OpenBao, only references in PG

### InfluxDB OSS (Time-Series)
- `telemetry` measurement — raw SNMP/gNMI fields per device (CPU/memory/uptime,
  interface counters; gNMI keyed `<InterfaceName>/<leaf>`)
- `interface_stats` measurement — derived per-interface bps/pps/util/error rates
  (tagged by if_index for SNMP, interface name for gNMI)
- `transit_latency` measurement — per-flow/link latency observations
- Environment sensors (temp/fan/power) surfaced from `telemetry` when reported

### OpenSearch (Logs & Flows)
- Normalized syslog documents
- Log pattern frequency metrics
- NetFlow/sFlow records
- Auth event search and anomaly detection queries

### NATS JetStream Topics
- `netpulse.telemetry.{device_id}.metrics`
- `netpulse.telemetry.{device_id}.syslog`
- `netpulse.telemetry.{device_id}.flow`
- `netpulse.alerts.{severity}`
- `netpulse.config.{device_id}.collected`
- `netpulse.auth.events`

---

## Development Roadmap

### Phase 1 — Foundation ✅
- [x] Architecture design
- [x] Technology stack selection and license audit
- [x] Development environment (WSL2, Docker, Claude Code)
- [x] GitHub repository initialized
- [x] Docker Compose scaffold — 22 services
- [x] Infrastructure services running and healthy
- [x] Django backend — apps, models, REST API, JWT auth, RBAC
- [x] ingest-grpc — gRPC/gNMI + Cisco MDT dial-out receiver
- [x] ingest-snmp, ingest-syslog, ingest-flow, ingest-otlp, ingest-api-poller
- [x] stream-processor — NATS → InfluxDB/OpenSearch/PostgreSQL
- [x] First end-to-end test with a real device (Cisco C8000V)

### Phase 2 — Core Intelligence
- [x] Config compliance engine (Jinja2)
- [x] Bandwidth trending (derived interface bps/pps/util)
- [x] Basic alerting end-to-end (rules, events, channels, NATS)
- [x] SNMP polling pipeline
- [x] gNMI / Cisco MDT streaming
- [ ] Forecasting / 95th-percentile capacity planning

### Phase 3 — Advanced Intelligence
- [x] Log ingestion + OpenSearch query (basic anomaly groundwork)
- [x] Auth security engine (basic — DeviceRiskScore)
- [x] Alert routing — teams/policies/route-matching + email/Slack/Discord
- [x] Alert auto-resolution (state-driven, label-matched recovery + 90-day purge)
- [x] Maintenance windows (alert suppression, one-off + recurring)
- [x] Topology dedup (canonical link ordering + 4-field UniqueConstraint)
- [x] FortiOS interface discovery (platform-aware parser)
- [x] SNMPv3 authPriv config generation (per-platform)
- [x] Auth rate limiting (H1 — JWT endpoint throttling)
- [x] CVE ingestion + applicability engine
- [x] Lifecycle/EOL management (stub)
- [ ] 🔄 SSO authentication (Stage 1 — Google OAuth2 — in progress)
- [x] Default/system alert rules (seed_alert_rules; disable-to-suppress)
- [x] Admin user management (/api/users/ CRUD; self/last-admin delete guards)
- [x] Discovery page wiring — DiscoveryJob API + approve/reject + OT/ICS exclusions
- [ ] Log group-trend / vendor-bug detection

### Phase 4 — Frontend & Flow
- [x] React scaffold + live dashboards
- [x] Live telemetry charts
- [x] Interface traffic (bps/pps/errors)
- [x] Topology map (LLDP)
- [x] Agentless service checks (7 handlers) + history panels + dashboard widget
- [~] NetFlow/sFlow path latency visualisation (D3) — in progress
- [x] Budget/security reports (partial)
- [x] Unified risk score (stub)

### Phase 5 — Polish & Community
- [ ] spane Collector (on-prem agent)
- [ ] Helm chart for Kubernetes
- [ ] Documentation site
- [ ] Public announcement

---

## Telemetry Pipeline

Two protocols run simultaneously per device, reconciled by the stream-processor:

- **SNMP polling** (fallback) — ingest-snmp polls device + interface OIDs on an
  interval (default 5 min). Fields are written to the `telemetry` measurement
  keyed `<oid_name>_<ifIndex>` (e.g. `ifHCInOctets_2`).
- **Cisco MDT / gNMI streaming** (preferred) — ingest-grpc receives dial-out
  telemetry on port **57400**. Cisco IOS-XE/XR use **Model-Driven Telemetry**
  over a Cisco-specific gRPC dialout, not standard OpenConfig gNMI dialout:
  - protos: `mdt_grpc_dialout.proto` + `cisco_telemetry.proto`
  - flattened field format: `<InterfaceName>/<metric>` (e.g.
    `GigabitEthernet1/in_octets`)

The stream-processor derives per-interface bps/pps/util/error rates from counter
deltas (`interface_stats` measurement) for both shapes — SNMP rows are tagged by
ifIndex, gNMI rows by interface name. When a device is actively streaming gNMI,
SNMP polling of the same metrics can be skipped (adaptive polling, planned).

---

## Service Checks (Agentless Synthetic Monitoring)

spane probes services externally — no agent on the target.

- **Model**: `ServiceCheck` (type, host/port, interval, optional device + site
  association, thresholds, state) and `CheckResult` (per-probe status/latency).
- **Engine**: `check-engine` (`run_check_engine`) — an asyncio scheduler that
  runs due checks concurrently, records results, advances each check's state
  machine and raises NATS alerts on transitions (down → high, recovery → info,
  degraded → medium). A down alert is suppressed when the associated device is
  itself unreachable.
- **Handlers (implemented)**: HTTP/HTTPS (aiohttp), TCP (asyncio), ICMP
  (icmplib — packet loss/RTT/jitter), DNS (aiodns), TLS (stdlib ssl — cert
  days_remaining/CN/issuer), SMTP (aiosmtplib — connect+EHLO), SSH-banner
  (asyncio TCP). Latency thresholds classify up/degraded/down for latency
  types; ICMP/DNS/TLS own their status (loss / answer match / cert expiry).
  `failures_before_alert` suppresses flaps.
- **API**: `/api/checks/` CRUD + `run-now/`, `results/` (history + uptime
  summary), `summary/`. UI: `/checks` with per-check expanding history panels
  (response-time chart, status timeline) + a cert-expiry dashboard widget.

FTP/LDAP check_types are defined on the model but have no handler yet.

---

## Alert Routing & Escalation (Stage 1)

`apps/alerting/` routes alerts to teams via escalation policies.

- **Default rules**: `apps/alerts` `seed_alert_rules` (run from the api
  entrypoint) seeds the six `is_system` rules the engines actually emit
  (`Interface State Change`, `device-unreachable`, `service-check-failed`,
  `flow`/`latency-threshold-exceeded`, `log-anomaly-detected`). System rules are
  protected from deletion; setting `is_active=False` suppresses their alerts —
  both `stream-processor._db_write_alert` and `interface_monitor` skip event
  creation for a disabled rule.
- **Models**: Team/TeamMember, ContactMethod, EscalationPolicy/EscalationStep,
  AlertRoute (severity/source/check-type/site match conditions, AND logic,
  priority-ordered), AlertNotification.
- **Matching**: first active route (ascending priority) whose conditions all
  match; an empty condition list means "match all".
- **Notifications**: email (Django mail backend) plus per-team **Slack** and
  **Discord** webhooks. The engine fires the policy's first step to the team's
  email-opted-in members (preferring the current on-call user) and posts a
  colour-coded embed to the team's Discord/Slack webhook. Each delivery is
  recorded as an AlertNotification (channel = email/slack/discord).
- **Maintenance windows**: `MaintenanceWindow` (one-off + daily/weekly/monthly
  recurrence, device/site scope, severity/check-type filters) suppresses both
  the publishing monitor and the routing engine while active.
- **Auto-resolution**: alerts carry a `state` (firing/resolved) with
  `resolved_by`/`resolution_note`. Reachability/check/interface recovery
  auto-resolves matching firing events by label (`labels__source`,
  `labels__device_id`); a 90-day purge runs from the scheduler. The list API
  defaults to active-only (`?resolved=false|true|all`).
- **On-call**: OnCallSchedule/Shift with current-on-call resolution;
  acknowledgement/snooze on AlertEvent.
- **API**: `/api/alerting/` — teams (+members, +`test-discord/`), policies
  (+steps), routes (+`test/`), maintenance windows (+`active/`, +`end-now/`),
  on-call schedules, notifications. UI: Settings → Alert Routing.
- **Later stages**: PagerDuty/Webhook/SMS, visual escalation builder + on-call
  calendar.

---

## Supported Platforms

### Fully Supported
| Platform | SNMP | SSH | Config Backup | ARP/MAC | Environment |
|----------|------|-----|---------------|---------|-------------|
| Cisco IOS/IOS-XE | ✅ | ✅ | ✅ | ✅ | ❌ |
| HPE AOS-CX | ✅ | ✅ | ✅ | ✅ | ✅ |
| Fortinet FortiOS | ✅ | ✅ | ✅ | ✅ | ❌ |
| SonicWall (v7) | ✅ | ✅ | ⚠️ admin only | ✅ | ❌ |
| SonicWall (v8) | ✅ | ✅ | ✅ | ✅ | ❌ |

### Notes
- **SonicWall v7 config backup** via the REST API requires the built-in `admin`
  account. User accounts return `API_AUTH_USER_CAN_MGMT` and cannot access the
  `/config/current` endpoint (401, even with FULL_ADMIN privilege). Workaround:
  use the SSH CLI backup, or obtain built-in admin credentials.
- **SonicWall environment data** (temperature / fans / PSU) is not exposed via
  SNMP or REST API on any version — the Environment tab correctly shows
  "No environment data" for SonicWall. CPU/Memory are available on the
  Telemetry tab.
- **AOS-CX**: Aruba Central keepalive logs are normal and expected
  (`hpe-restd` AMM messages, roughly every 30 s — cloud-management heartbeats,
  not errors). They can be hidden with Log Filters.

See [docs/platforms/sonicwall.md](platforms/sonicwall.md) and
[docs/platforms/aos_cx.md](platforms/aos_cx.md) for full per-platform guides.

---

## Known Platform Compatibility

**Tested and working**
- Cisco C8000V (IOS-XE 17.12.04) — virtual router
  - SNMP v3: working
  - Cisco MDT / gNMI streaming: working (port 57400)
  - SSH: working
  - Syslog: working
  - NetFlow v9: configured
  - Note: as a virtual platform it reports no physical fan/power/temperature
    sensors, so environment tiles are correctly empty for it.

**Configured but not yet validated against real hardware**
- Juniper JunOS (JTI `set services analytics` telemetry config generation)
- Arista EOS, Cisco NX-OS, Cisco IOS-XR
- Palo Alto PAN-OS (via OTLP)
- Fortinet FortiOS:
  - SSH: working
  - Interface discovery: working (platform-aware — `get system interface`
    with a custom parser, not Cisco `show interfaces`; LLDP via `get system
    lldp neighbors-detail` best-effort)
  - SNMPv3: config generated (authPriv per FortiOS syntax)
  - SNMPv2c: config generated (with plaintext warning)
  - Syslog: working
  - gNMI: not supported (documented — FortiOS has no gNMI dial-out)
  - NetFlow: configured

### SNMP Security
spane generates SNMPv3 authPriv configurations by default. SNMPv2c community
strings are transmitted in plaintext and should not be used in production
environments; the UI shows a warning when an SNMPv2c credential is configured.
Per-platform CLI token differences are handled by the generator (IOS-XE
"aes 128", NX-OS "aes-128", Junos "privacy-aes128", EOS/FortiOS "aes").

---

## Planned Features (not yet implemented)

Designed but with no models/endpoints/services yet — do not treat as current:

- **BGP looking glass** — passive, read-only BGP route collector (e.g. ExaBGP);
  session state + routing table + prefix-change alerting. Planned models
  BGPSession/BGPPrefix, service `bgp-monitor`, endpoints `/api/bgp/`.
- **Alert routing beyond Stage 1** — on-call schedules, acknowledgement/snooze,
  Slack/PagerDuty/Webhook/SMS channels, escalation/on-call UI.
- **Adaptive polling** — skip SNMP for metrics a device already streams via gNMI.

---

## Design Principles

1. **Push-first, poll as fallback** — designed for streaming, not polling
2. **Security first** — OpenBao for all secrets, mTLS everywhere, audit everything
3. **Multi-tenant from day one** — useful for MSPs and cloud-hosted version
4. **Schema-flexible device metadata** — JSONB makes onboarding new device types easy
5. **Pluggable alert channels** — Slack, PagerDuty, email, webhooks
6. **Single command deployment** — `docker compose up` should just work
7. **Use the right tool** — don't reinvent what open source already does well
8. **License clean** — every dependency audited, zero commercial license exposure
9. **Community first** — vendor advisories, SNMP walks, TextFSM templates all contributable

---

*Document created from initial architecture design session — May 2026*
*Keep this document updated as the platform evolves*

---

## SNMP Trap Receiver

Traps are a completely different flow from polling — devices initiate contact
when events occur rather than waiting to be asked.

### Critical Use Cases
- **UPS events** — on battery, low battery, battery restored, overload, shutdown imminent
- **Link state** — interface up/down (ifOperStatus change)
- **Routing** — BGP neighbor state change, OSPF neighbor loss
- **Hardware** — fan failure, PSU failure, temperature threshold
- **Security** — authentication failure notifications
- **Environmental** — temperature, humidity, PDU alerts

### Architecture
Device/UPS
│
│ SNMP Trap (UDP 162)
▼
ingest-snmp (trap receiver mode)
│
│ Normalized trap event → NATS
▼
netpulse.telemetry.{device_id}.trap
│
├── stream-processor (correlation)
├── alert-engine (immediate notification)
└── OpenSearch (searchable history)

### Trap Types Supported
- **SNMPv1 traps** — legacy UPS, old gear (fire and forget)
- **SNMPv2c traps** — most common (fire and forget)
- **SNMPv3 informs** — modern, authenticated, acknowledged (device retries until confirmed)

### MIB Support
- RFC 1628 — UPS MIB (standard, all vendors)
- APC MIB — APC/Schneider specific (common in datacenters)
- Eaton MIB — Eaton UPS specific
- Standard network MIBs — IF-MIB, BGP4-MIB, OSPF-MIB

### Key Design Notes
- Polling and trap reception run in the same ingest-snmp service
- Traps arrive on UDP 162 (privileged — use override file to remap in dev)
- SNMPv3 informs require acknowledgment unlike v1/v2c
- Unknown OIDs logged and stored raw — MIB coverage expandable by community

---

## Device Discovery

### Overview
Four-tier system — all discovered devices require admin approval before
activation. Never auto-activate without human review.

### Tier 1 — Passive (always running)
Ingest layer detects new source IPs automatically:
- New syslog source → PENDING device
- New gNMI dial-out → PENDING device  
- New NetFlow exporter → PENDING device
- New SNMP trap source → PENDING device

### Tier 2 — Topology Walk (most powerful)
Seed one device, recurse through entire network:
- CDP/LLDP neighbor walk
- Route table next-hop recursion
- ARP table host discovery
- BGP peer discovery
- MAC table L2 discovery

**Why route tables over ping sweeps:**
Enterprise networks block ICMP by policy but routing infrastructure
is fully reachable via SNMP/NETCONF. Pull ipRouteTable (SNMP),
ietf-routing (NETCONF), or "show ip route" (SSH+TextFSM) and probe
each next-hop. Finds devices that ping sweeps completely miss.

### Tier 3 — Active Scanning
Protocol probe sequence per discovered IP:
1. SNMP v2c/v3 → sysDescr, sysName
2. gNMI Capabilities RPC
3. NETCONF hello
4. SSH banner grab → parse vendor
5. HTTP/HTTPS → NX-API, EOS API detection
6. DNS reverse lookup
7. ICMP — last resort, expected to fail often

### Tier 4 — Import
NetBox API (with a dry-run preview), UniFi controllers (per-controller, or auto-discovered from a
UniFi Site Manager cloud account), Cisco DNA Center, CSV bulk import, manual entry. Imported/
discovered devices inherit a SiteCredential when their site has one. Integration secrets (NetBox
token, UniFi/SMTP credentials, cloud API key) live in OpenBao, never the database.

### Confidence Scoring (0-100)
| Score | Meaning |
|---|---|
| 100 | SNMP + CDP/LLDP + gNMI all confirmed |
| 60 | SNMP responds, sysDescr parsed |
| 30 | IP in route table, nothing confirmed |
| 10 | IP in ARP table only |

### Safety Controls
- **allowed_subnets** — never probe outside defined ranges
- **excluded_subnets** — explicitly exclude OT/ICS/SCADA networks
- **rate_limit_pps** — default 10 pps, be polite to the network
- **max_depth** — default 10 hops, prevent runaway recursion
- **max_devices** — default 1000, circuit breaker per job

### OT/ICS WARNING
Never auto-probe OT/ICS subnets. PLCs, SCADA systems, and industrial
controllers may crash or cause physical damage if probed unexpectedly.
Prompt admin to identify and exclude OT subnets during initial setup.

---

## Device Discovery

### Overview
Four-tier system — all discovered devices require admin approval before
activation. Never auto-activate without human review.

### Tier 1 — Passive (always running)
Ingest layer detects new source IPs automatically:
- New syslog source → PENDING device
- New gNMI dial-out → PENDING device  
- New NetFlow exporter → PENDING device
- New SNMP trap source → PENDING device

### Tier 2 — Topology Walk (most powerful)
Seed one device, recurse through entire network:
- CDP/LLDP neighbor walk
- Route table next-hop recursion
- ARP table host discovery
- BGP peer discovery
- MAC table L2 discovery

**Why route tables over ping sweeps:**
Enterprise networks block ICMP by policy but routing infrastructure
is fully reachable via SNMP/NETCONF. Pull ipRouteTable (SNMP),
ietf-routing (NETCONF), or "show ip route" (SSH+TextFSM) and probe
each next-hop. Finds devices that ping sweeps completely miss.

### Tier 3 — Active Scanning
Protocol probe sequence per discovered IP:
1. SNMP v2c/v3 → sysDescr, sysName
2. gNMI Capabilities RPC
3. NETCONF hello
4. SSH banner grab → parse vendor
5. HTTP/HTTPS → NX-API, EOS API detection
6. DNS reverse lookup
7. ICMP — last resort, expected to fail often

### Tier 4 — Import
NetBox API (with a dry-run preview), UniFi controllers (per-controller, or auto-discovered from a
UniFi Site Manager cloud account), Cisco DNA Center, CSV bulk import, manual entry. Imported/
discovered devices inherit a SiteCredential when their site has one. Integration secrets (NetBox
token, UniFi/SMTP credentials, cloud API key) live in OpenBao, never the database.

### Confidence Scoring (0-100)
| Score | Meaning |
|---|---|
| 100 | SNMP + CDP/LLDP + gNMI all confirmed |
| 60 | SNMP responds, sysDescr parsed |
| 30 | IP in route table, nothing confirmed |
| 10 | IP in ARP table only |

### Safety Controls
- **allowed_subnets** — never probe outside defined ranges
- **excluded_subnets** — explicitly exclude OT/ICS/SCADA networks
- **rate_limit_pps** — default 10 pps, be polite to the network
- **max_depth** — default 10 hops, prevent runaway recursion
- **max_devices** — default 1000, circuit breaker per job

### OT/ICS WARNING
Never auto-probe OT/ICS subnets. PLCs, SCADA systems, and industrial
controllers may crash or cause physical damage if probed unexpectedly.
Prompt admin to identify and exclude OT subnets during initial setup.

## API-Based Platform Integrations

### The Problem
Cloud-managed devices have no SNMP agent, no SSH access, no gNMI —
all data lives in vendor cloud APIs. You query their cloud, not the device.

### Platforms Supported

| Vendor | Platform | API Type | Key Data |
|---|---|---|---|
| Cisco | Meraki Dashboard | REST | Device status, uplink health, traffic, alerts |
| Juniper | Mist AI | REST + WebSocket | RF health, client experience, WAN assurance |
| HPE | Aruba Central | REST | AP health, client count, RSSI, WAN stats |
| Ubiquiti | UniFi Network | REST | AP/switch health, client data, port stats |
| Cisco | DNA Center | REST | Network topology, device inventory, issues |
| Fortinet | FortiCloud | REST | Device health, security events |
| Palo Alto | Panorama | REST/XML | Firewall health, threat logs |
| Cradlepoint | NetCloud | REST | LTE/5G WAN health, signal quality |

### Architecture — ingest-api-poller Service
Vendor Cloud APIs
│
│ HTTPS REST / WebSocket
▼
ingest-api-poller
├── Scheduled polling per vendor/org
├── Webhook receiver (vendor pushes events to us)
├── Rate limit awareness per vendor API
├── Delta detection — only publish changes
├── Credentials via OpenBao, never stored locally
└── Normalize to common schema → NATS
│
▼
netpulse.telemetry.{device_id}.metrics
netpulse.telemetry.{device_id}.events

### Two Ingestion Modes

**Mode 1 — Polling** (all vendors)
Every 60-300 seconds:
GET /api/v1/devices → normalize → NATS

**Mode 2 — Webhooks** (preferred where supported)
Vendor pushes events to spane endpoint:
POST /webhooks/{vendor} → normalize → NATS
Meraki, Mist, and UniFi all support webhooks → near real-time

### Plugin Architecture

```python
class VendorAPIPlugin:
    name: str
    vendor: str

    async def authenticate(self, credentials: dict) -> bool
    async def get_devices(self) -> list[Device]
    async def get_metrics(self, device_id: str) -> dict
    async def handle_webhook(self, payload: dict) -> list[Event]
    async def get_rate_limit(self) -> RateLimit

# Community-contributable implementations
class MerakiPlugin(VendorAPIPlugin): ...
class MistPlugin(VendorAPIPlugin): ...
class UniFiPlugin(VendorAPIPlugin): ...
class CradlepointPlugin(VendorAPIPlugin): ...
```

Plugin model means community can add new vendors without touching core platform.

### MSP Consideration
Meraki and Mist have multi-org APIs — one credential set manages multiple
customer organizations. Critical for MSP deployments of spane where a
single platform monitors multiple customers simultaneously.

### Integration with Platform Features

- **Unified risk score** — Meraki AP offline, Mist WAN alert all factor in
- **Lifecycle management** — pull device inventory from vendor APIs, match EOL database
- **Config compliance** — SSID consistency, network policy validation across cloud-managed devices
- **Bandwidth planning** — WAN metrics from Meraki/Mist feed capacity forecasting
- **Bandwidth planning** — Cradlepoint LTE failover triggers cost/bandwidth alerts

### Vendor API Notes

**Meraki**
- Rate limit: 10 req/sec per org
- Webhooks: supported for alerts and device status
- MSP: Dashboard API supports multiple orgs

**Mist / Juniper**
- WebSocket streaming for real-time events
- Mist AI anomaly detection — consume their alerts natively

**UniFi**
- Local controller API (self-hosted) or UniFi Cloud
- Cookie-based auth on local controller

**Cradlepoint**
- Key data: LTE/5G signal strength, carrier, WAN health
- Critical for branch offices on cellular WAN or backup

## ChatOps Integration

> **Operator guide:** see [Integrations → ChatOps](integrations/chatops.md) for
> the in-UI "Ask spane" chat, the external chat-platform setup (Slack/Teams/
> Google Chat/Discord/Mattermost), host/TLS requirements, and the optional local
> NLP model.

### Overview
Engineers query spane directly from chat platforms using natural language.
No need to open a dashboard for quick health checks.

### Example Interaction
Engineer: "@netpulse status of router-a"
spane: 🟡 Router-A (WAN Edge | Datacenter-1)
├── Status: reachable
├── CPU: 34% · Memory: 51%
├── CVE Exposure: 2 medium, 0 critical
├── Risk Score: 42/100 (moderate)

### Supported Platforms
Microsoft Teams, Slack, Google Chat, Discord, Mattermost

### Query Types
The built-in intents (see the [operator guide](integrations/chatops.md#built-in-commands)
for exact phrasings):
- Device status and health
- Device list (down / up / all, optionally scoped to a site)
- Site status
- Active alerts
- CVE exposure
- EOL / lifecycle status
- Help

Action commands (config push from chat, with an approval workflow) are **planned**
— the current built-in intents are read-only queries.

### Architecture
Thin chatops-service sits on top of Django API:
- Webhook receivers at /api/webhooks/{platform}/
- Intent parser maps natural language → API calls
- Response formatter per platform
- No business logic — pure translation layer
- Optional NLP fallback for unmatched messages — self-hosted (Ollama) or a
  hosted API (e.g. Anthropic Claude); off by default (`nlp_provider`)

### Proactive Notifications
Push alerts to designated channels without being asked:
- Critical alerts and incidents
- CVE notifications affecting inventory
- UPS on-battery events
- Circuits approaching capacity
- EOL approaching deadlines

### Security
- Chat user identity mapped to spane RBAC
- Sensitive data never in chat responses
- Action commands require explicit approval
- All queries audit logged
- Responses restricted to approved channels

---

## Recent Additions (implemented)

This section summarizes capabilities added since the original design above. The
living, authoritative status list lives in
[CLAUDE.md](https://github.com/travisjohnsonga/netpulse/blob/main/CLAUDE.md)
("Current Status").

- **Adaptive SNMP/gNMI polling** — ingest-grpc stamps a Valkey heartbeat
  (`gnmi:last_seen:{id}`, TTL 180s). While gNMI streams, ingest-snmp polls only
  the essential system OIDs (`ALWAYS_POLL_OIDS`: sysUpTime/sysDescr/sysName/
  sysLocation — gNMI doesn't carry uptime) and suppresses the rest, auto-resuming
  the full poll when the stream stalls. `ADAPTIVE_POLLING=false` disables it.
- **Collection-status API** — `GET /api/devices/{id}/collection-status/` reports
  gNMI/SNMP active state, last-seen, metrics-per-push and the suppressed flag;
  device-header 📡/📊 badges refresh every 60s.
- **Discovery → enrichment pipeline** — nmap-based active scan; approve creates a
  PENDING→ACTIVE device, then enrichment runs SNMP/SSH info → interface discovery
  (auto-add LLDP-connected ports, SNMPv3 supported) → LLDP topology → initial
  config baseline. Steps are independent. FortiOS detected from sysDescr;
  unknown-platform devices with a known vendor are bulk-approvable.
- **Config collection** — initial baseline on approval + a scheduled run at
  `CONFIG_COLLECTION_HOUR_1/_2` (default 07:00 & 19:00 UTC) with change detection
  and a "Config Changed" alert.
- **Ping latency** — reachability-monitor stores TCP RTT in InfluxDB
  (`device_reachability`); `GET /api/devices/{id}/reachability/`; Telemetry chart,
  Overview tile, dashboard sparklines, latency alert rules. Liveness probes
  TCP/22 then TCP/443 (firewalls blocking SSH still register live).
- **FortiGate SNMP** — fgSysCpuUsage/MemUsage/MemCapacity surfaced as CPU/memory.
- **Topology dedup** — interface names canonicalised (Gi3 → GigabitEthernet3) so
  the same physical link isn't recorded twice across SNMP/SSH discovery.
- **Hostname display** — optional domain-suffix stripping for display only
  (`display_hostname`); full hostname retained for SSH/SNMP/syslog.
- **Team notification targets** — system users are first-class targets
  (per-member email/Slack/Discord opt-in + profile chat handles).
- **MIB support** — `mibs/` tree (standard/vendor/community/custom) mounted into
  api + ingest-snmp; `/api/mibs/` (list/upload/delete/resolve);
  `scripts/download_mibs.sh`; Settings → MIB Files.
- **Platforms** — added SonicWall SonicOS, HPE AOS-CX, Aruba AOS (OIDs, field
  mapping, sysDescr/banner detection).
- **HPE AOS-CX environment telemetry (SNMP-only)** — validated against a real
  AOS-CX 6100 (wco2-idf5-asw-01). AOS-CX exposes environment metrics at
  vendor-specific indexes, so the poller gained table **WALK** support
  (`walk_oids` in the device payload) alongside the existing GETs:
  - CPU: `hrProcessorLoad` (1.3.6.1.2.1.25.3.3.1.2) lives at vendor indexes
    (196608/196609), not `.1` — walked and averaged.
  - Memory: `hrStorage` index 1 ("Physical memory") via GET → `memory_used_pct`
    + bytes.
  - Temperature: ENTITY-SENSOR-MIB (1.3.6.1.2.1.99.1.1.1.*); celsius =
    value × 10^scale × 10^-precision (28875 milli → 28.875 °C).
  - Fan/PSU: presence/count from ENTITY-MIB `entPhysicalClass` (fan=7, psu=6).
  Derivation lives in `apps/telemetry/snmp_environment.py` (pure, unit-tested);
  the stream-processor merges scalars (`cpu_pct`, `memory_used_pct`,
  `memory_*_bytes`, `temp_max_c`, `fan_count`, `psu_count`) into the `telemetry`
  measurement and writes one **`device_environment`** point per temperature
  sensor (tags `device_id`, `sensor_name`, `sensor_type`; fields `temperature_c`,
  `status_ok`). Temperature alert rules are seeded: **High Temperature Warning**
  (medium, ≥75 °C), **High Temperature Critical** (critical, ≥85 °C),
  **Temperature Sensor Failed** (high).
  - **6100 limitations**: per-unit fan RPM is unavailable (rpm sensors read -1)
    and there is no standard per-unit fan/PSU oper-status (the entPhysical `.8`
    column is `entPhysicalHardwareRev`, not status) — so fan/PSU is reported as
    presence/count only. The 6100 also does **not** support the AOS-CX REST API
    (login 400/401); higher-end models (8xxx) may. SSH banner is generic OpenSSH.
  - **SNMPv3 reliability**: the poller creates a fresh `SnmpEngine` per poll
    (avoids stale engineBoots/engineTime — general robustness). The "Wrong SNMP
    PDU digest" observed in the lab was a **wrong stored SNMPv3 key**, not the
    engine: the credential profile's auth/priv passphrase in OpenBao did not
    match the device. With the correct key both pysnmp 6.3 (ingest) and 7.1
    (api) succeed; fix is to update the credential in Settings → Credentials.
