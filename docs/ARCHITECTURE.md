# NetPulse Architecture & Design Document

> This document captures the full architecture, design decisions, and requirements
> defined during the initial project design session. It serves as the authoritative
> reference for all development decisions.

---

## Project Vision

A push-first, open source network intelligence platform that solves real problems
ignored by traditional monitoring tools. Built for modern infrastructure, vendor-agnostic,
deployable on-prem via Docker Compose or cloud-hosted via Kubernetes.

**Key Differentiator:** Most platforms poll devices on a schedule. NetPulse is built
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

### 10. NetPulse Collector (On-Prem → Cloud Agent)

Lightweight agent for securely forwarding on-prem telemetry to cloud-hosted NetPulse.

**Architecture:**
- Runs on-prem as Docker container or systemd service
- Receives all telemetry locally (gRPC/gNMI, syslog, NetFlow/sFlow, SNMP polling)
- Forwards to cloud over single outbound mTLS connection (port 443/8443)
- No inbound firewall rules required on customer network
- Local disk buffer if cloud connection drops — replays when reconnected

**Security:**
- Outbound only — customer opens no inbound ports
- mTLS — both collector and cloud authenticate with certificates
- Certificates issued by OpenBao PKI engine
- Unique API key per collector instance (stored as bcrypt hash)
- TLS 1.3 minimum

**Customer deployment:**
```bash
docker run -d \
  --name netpulse-collector \
  --restart always \
  -p 514:514/udp \
  -p 2055:2055/udp \
  -p 50051:50051 \
  -e NETPULSE_CLOUD_URL=https://cloud.netpulse.io \
  -e NETPULSE_API_KEY=their-api-key \
  netpulse/collector:latest
```

**Solves SNMP behind firewall:** Collector polls local devices directly and forwards
results to cloud. No firewall holes needed for SNMP.

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
| Frontend | React | MIT | |
| Charting | Apache ECharts | Apache 2.0 | |
| Config templates | Jinja2 | BSD | |
| CLI parsing | TextFSM / ntc-templates | Apache 2.0 | show-command parsing |
| Stream processing | asyncio + nats-py | Apache 2.0 | NATS consumers (Django mgmt commands) |
| gRPC | grpcio | Apache 2.0 | |
| SNMP | pysnmp | BSD | |
| Async HTTP | aiohttp | Apache 2.0 | service-check HTTP/HTTPS probes |
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
| `scheduler` | Cron jobs and report generation | services/api |
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

---

## Data Architecture

### PostgreSQL (Primary Database)
- Device inventory (Device incl. reachability state: is_reachable, unreachable_since)
- Sites, device groups, TopologyLink (LLDP), discovery jobs/results
- CredentialProfile metadata (secrets live in OpenBao, never here)
- CVE (CVE, DeviceCVE), LifecycleMilestone, DeviceRiskScore
- Alert rules/events/channels, config backup settings + DeviceConfig metadata
- ServiceCheck + CheckResult (agentless monitoring)

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
- [ ] 🔄 CVE ingestion + applicability (in progress)
- [ ] 🔄 Lifecycle/EOL management (in progress)
- [ ] Log group-trend / vendor-bug detection

### Phase 4 — Frontend & Flow
- [x] React scaffold + live dashboards
- [x] Live telemetry charts
- [x] Interface traffic (bps/pps/errors)
- [x] Topology map (LLDP)
- [x] Agentless service checks (HTTP/HTTPS/TCP) + dashboard widget
- [ ] NetFlow/sFlow path latency visualisation (D3)
- [ ] Budget/security reports, unified risk score UI

### Phase 5 — Polish & Community
- [ ] NetPulse Collector (on-prem agent)
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

NetPulse probes services externally — no agent on the target.

- **Model**: `ServiceCheck` (type, host/port, interval, optional device + site
  association, thresholds, state) and `CheckResult` (per-probe status/latency).
- **Engine**: `check-engine` (`run_check_engine`) — an asyncio scheduler that
  runs due checks concurrently, records results, advances each check's state
  machine and raises NATS alerts on transitions (down → high, recovery → info,
  degraded → medium). A down alert is suppressed when the associated device is
  itself unreachable.
- **Stage 1 handlers (implemented)**: HTTP/HTTPS (aiohttp) and TCP
  (asyncio.open_connection). Latency thresholds classify up/degraded/down;
  `failures_before_alert` suppresses flaps.
- **API**: `/api/checks/` CRUD + `run-now/`, `results/`, `summary/`.

The `ServiceCheck` model already defines ICMP/DNS/TLS/SMTP/SSH/FTP/LDAP types;
their handlers are planned (see Planned Features).

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
- Palo Alto PAN-OS (via OTLP), Fortinet FortiOS (via SNMP)

---

## Planned Features (not yet implemented)

Designed but with no models/endpoints/services yet — do not treat as current:

- **BGP looking glass** — passive, read-only BGP route collector (e.g. ExaBGP);
  session state + routing table + prefix-change alerting. Planned models
  BGPSession/BGPPrefix, service `bgp-monitor`, endpoints `/api/bgp/`.
- **Endpoint discovery** — MAC address-table + ARP-table ingestion (SSH/SNMP),
  OUI vendor lookup, find-device-by-IP/MAC. Planned models MACEntry/ARPEntry,
  endpoint `/api/endpoints/`.
- **Service checks beyond Stage 1** — ICMP (icmplib), DNS (aiodns), TLS, SMTP
  (aiosmtplib), SSH handlers.
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
NetBox API, Cisco DNA Center, CSV bulk import, manual entry.

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
NetBox API, Cisco DNA Center, CSV bulk import, manual entry.

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
Vendor pushes events to NetPulse endpoint:
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
customer organizations. Critical for MSP deployments of NetPulse where a
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

### Overview
Engineers query NetPulse directly from chat platforms using natural language.
No need to open a dashboard for quick health checks.

### Example Interaction
Engineer: "@netpulse status of router-a"
NetPulse: 🟡 Router-A (WAN Edge | Datacenter-1)
├── Uptime: 47 days
├── CPU: 34% (normal)
├── WAN Interface: 78% ⚠️ (trending to capacity)
├── BGP Sessions: 3/3 up ✅
├── CVE Exposure: 2 medium, 0 critical
├── Risk Score: 42/100 (moderate)

### Supported Platforms
Microsoft Teams, Slack, Google Chat, Discord, Mattermost

### Query Types
- Device/site status and health
- Active alerts and incidents  
- CVE exposure queries
- EOL/lifecycle status
- Capacity and bandwidth queries
- Action commands (with approval workflow)

### Architecture
Thin chatops-service sits on top of Django API:
- Webhook receivers at /api/webhooks/{platform}/
- Intent parser maps natural language → API calls
- Response formatter per platform
- No business logic — pure translation layer
- Optional Claude API integration for richer NLP

### Proactive Notifications
Push alerts to designated channels without being asked:
- Critical alerts and incidents
- CVE notifications affecting inventory
- UPS on-battery events
- Circuits approaching capacity
- EOL approaching deadlines

### Security
- Chat user identity mapped to NetPulse RBAC
- Sensitive data never in chat responses
- Action commands require explicit approval
- All queries audit logged
- Responses restricted to approved channels
