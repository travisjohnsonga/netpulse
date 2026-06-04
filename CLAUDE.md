# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

NetPulse is a push-first, open source network intelligence platform. Core capabilities: gRPC/gNMI streaming telemetry ingest, config compliance, CVE intelligence, lifecycle management, log anomaly detection, and unified risk scoring. Status: early development — scaffold only.

Stack: Python 3.13, Django 6.0 (backend API), React (frontend), Docker Compose (all services).

## Architecture

The platform is composed of containerized services defined by `.env.example`:

| Service | Role |
|---|---|
| Django | REST/WebSocket API, business logic, task orchestration |
| PostgreSQL | Relational data (devices, configs, CVEs, lifecycle records) |
| InfluxDB | Time-series telemetry metrics |
| OpenSearch | Log storage and anomaly detection queries |
| Valkey | Cache and task queue (Redis-compatible) |
| NATS | Internal message bus between ingest and processing |
| OpenBao | Secrets management (HashiCorp Vault-compatible fork) |

**Ingest layer** receives streaming data on dedicated ports:
- gRPC/gNMI: port 57400 (structured telemetry from network devices)
- Syslog UDP/TCP: 514/601
- NetFlow: 2055
- sFlow: 6343

Protobuf-compiled files (`*_pb2.py`, `*_pb2_grpc.py`) are gitignored — regenerate them from `.proto` sources when needed.

## Service Layout

| Build context | Services |
|---|---|
| `./services/api` | `api`, `websocket`, `stream-processor`, `config-manager`, `alert-engine`, `cve-engine`, `lifecycle-engine`, `security-engine`, `scheduler` |
| `./services/frontend` | `frontend` |
| `./services/ingest` | `ingest-grpc`, `ingest-snmp`, `ingest-syslog`, `ingest-flow`, `ingest-otlp` |

All services run on the `netpulse-net` bridge network. Only ports needed by external traffic are exposed to the host (API, WebSocket, frontend, ingest endpoints). Infrastructure services (postgres, influxdb, opensearch, valkey, nats, openbao) are internal-only.

Processing engines (`stream-processor`, `*-engine`, `scheduler`) are Django management commands in `./backend`. Ingest services publish to NATS; the stream-processor fans data out to InfluxDB, OpenSearch, and PostgreSQL.

## Docker Compose Commands

```bash
# First run
cp .env.example .env                    # fill in all change-me values
cp docker-compose.override.yml.example docker-compose.override.yml

# Start everything
docker compose up -d

# Infrastructure only (useful while building app services)
docker compose up -d postgres influxdb opensearch valkey nats openbao

# Logs
docker compose logs -f api
docker compose logs -f --tail=100 stream-processor

# Rebuild a single service after code changes
docker compose build api && docker compose up -d --no-deps api

# OpenBao initialization (one-time after first `docker compose up openbao`)
docker compose exec openbao bao operator init     # save keys + root token securely
docker compose exec openbao bao operator unseal   # run 3× with different unseal keys
```

## Development Workflow

The application code is baked into the image (`COPY . .` — no source bind mount),
so **backend changes require an image rebuild before they run in the container.**
Editing a file on the host does not update the running service.

All services built from `./services/api` share one image. After a backend change,
rebuild that image once and recreate every api-based service:

```bash
# Rebuild the api image + recreate all api-based services (infra left running)
./netpulse.sh rebuild-api

# Rebuild + recreate just the frontend
./netpulse.sh rebuild-frontend
```

`rebuild-api` recreates these services with `--no-deps` (so postgres, nats, etc.
are not touched): `api websocket config-manager scheduler alert-engine cve-engine
lifecycle-engine security-engine stream-processor check-engine reachability-monitor`.

Migrations run automatically on api startup (entrypoint `migrate --noinput`).

Tests run inside the api container against in-memory SQLite (no external DB):

```bash
docker compose exec api python -m pytest -q            # full suite
docker compose exec api python -m pytest tests/test_checks.py -q
```

## Environment Setup

Copy `.env.example` to `.env` and fill in all `change-me` values before running any service.

### Admin credentials (dev)
- The Django admin password is set during `scripts/setup.sh`.
- Minimum 12 characters required (enforced by `scripts/setup.sh`).
- The configured value lives in `.env` (`DJANGO_SUPERUSER_PASSWORD`) after
  setup — never commit real passwords to git.
- Do NOT document actual passwords here or anywhere in the repo.

External integrations requiring API credentials (set in `.env`):
- NVD API key — CVE data feed
- Cisco PSIRT client ID/secret — Cisco advisory feed
- SMTP / Slack / PagerDuty — alerting

## NetPulse Collector (On-Prem Agent)

Lightweight agent deployed on-prem to securely forward telemetry to cloud-hosted NetPulse.

### Architecture
- Runs on-prem as a Docker container or systemd service
- Receives all telemetry locally (gRPC/gNMI, syslog, NetFlow/sFlow, SNMP)
- Forwards to cloud over a single outbound mTLS connection (port 443/8443)
- No inbound firewall rules required on customer network
- Local disk buffer if cloud connection drops — replays when reconnected

### Security
- Outbound only — customer opens no inbound ports
- mTLS — both collector and cloud authenticate with certificates
- Certificates issued by OpenBao PKI engine
- Unique API key per collector instance
- TLS 1.3 minimum

### Deployment (customer side)
docker run -d \
  --name netpulse-collector \
  --restart always \
  -p 514:514/udp \
  -p 2055:2055/udp \
  -p 57400:57400 \
  -e NETPULSE_CLOUD_URL=https://cloud.netpulse.io \
  -e NETPULSE_API_KEY=their-api-key \
  netpulse/collector:latest

### Cloud-Side Components
- collector-gateway service — authenticates collectors, routes to NATS
- Collector management in Django API — register, cert issuance, health monitoring
- OpenBao PKI — issues and rotates collector mTLS certificates

### Solves SNMP Behind Firewall
Collector polls local devices directly and forwards results to cloud.
No need to open SNMP ports through customer firewall.

## SNMP Trap Receiver
ingest-snmp must handle both polling AND trap reception:
- UDP port 162 for incoming traps (v1, v2c, v3 informs)
- MIBs: RFC 1628 (UPS), APC, Eaton, standard network MIBs
- Normalize to common trap schema → NATS topic: netpulse.telemetry.{device_id}.trap
- Critical use case: UPS on-battery notification, link state changes, hardware alerts
- SNMPv3 informs require acknowledgment (unlike v1/v2c fire-and-forget)

## SNMP Trap Receiver
ingest-snmp must handle both polling AND trap reception:
- UDP port 162 for incoming traps (v1, v2c, v3 informs)
- MIBs: RFC 1628 (UPS), APC, Eaton, standard network MIBs
- Normalize to common schema → NATS: netpulse.telemetry.{device_id}.trap
- Critical: UPS on-battery, link state changes, hardware alerts
- SNMPv3 informs require acknowledgment (unlike v1/v2c fire-and-forget)

## Device Discovery

Four-tier discovery system. All discovered devices land in PENDING state
requiring admin approval before becoming ACTIVE. Never auto-activate.

### Tier 1 — Passive (always running)
Auto-detect from ingest layer source IPs:
- New syslog source IP → PENDING device
- New gNMI dial-out connection → PENDING device
- New NetFlow/sFlow exporter → PENDING device
- New SNMP trap source → PENDING device

### Tier 2 — Topology Walk (seed-based, most powerful)
Seed one device, recurse through entire network:
- CDP/LLDP neighbor walk
- Route table next-hop recursion (critical — ICMP often blocked in enterprise)
- ARP table host discovery
- BGP peer discovery
- MAC table L2 discovery

Route table walking is preferred over ping sweeps — enterprise networks
block ICMP by policy but routing infrastructure is fully reachable via
SNMP/NETCONF. Pull ipRouteTable (SNMP), ietf-routing (NETCONF), or
"show ip route" (SSH+TextFSM) and probe each next-hop.

### Tier 3 — Active Scanning (subnet-based)
Admin defines subnets, system probes in order:
1. SNMP v2c/v3 → sysDescr, sysName (most reliable)
2. gNMI Capabilities RPC (port 50051/57344/57400)
3. NETCONF hello (port 830)
4. SSH banner grab (port 22) → parse vendor from banner
5. HTTP/HTTPS → NX-API, EOS API, FortiOS detection
6. DNS reverse lookup → hostname reveals device type
7. ICMP → last resort, expected to fail often

### Tier 4 — Import
- NetBox API integration
- Cisco DNA Center import
- CSV/JSON bulk import
- Manual entry via UI

### Discovery Confidence Score (0-100)
- 100: SNMP sysDescr + CDP/LLDP + gNMI capabilities confirmed
- 60:  SNMP responds, sysDescr parsed
- 30:  IP seen in route table, nothing confirmed yet
- 10:  IP in ARP table only, no protocol response

### Models Needed
- DiscoveryJob: subnet, method, allowed_subnets, excluded_subnets,
  max_depth, max_devices, rate_limit_pps, status, devices_found
- DiscoveredDevice: source_ip, detection_methods, responds_to (JSONField),
  confidence_score, discovered_hostname, discovered_vendor,
  discovered_platform, status (pending/approved/rejected), approved_by

### Safety Controls
- allowed_subnets: never probe outside defined ranges
- excluded_subnets: explicitly exclude OT/ICS/SCADA networks
  (PLCs and industrial controllers may crash or malfunction if probed)
- rate_limit_pps: be polite — default 10 packets/second
- max_depth: prevent runaway recursion — default 10 hops
- max_devices: circuit breaker — default 1000 devices per job

### OT/ICS WARNING
Never auto-probe OT subnets. Prompt admin to identify and exclude
OT/ICS subnets during initial setup. Physical damage possible if
industrial controllers are probed unexpectedly.

## API-Based Platform Integrations (Phase 3+)

Cloud-managed and API-only platforms that cannot use SNMP/gNMI:

Vendors: Meraki, Mist/HPE Aruba, UniFi, Cisco DNA Center,
Fortinet FortiCloud, Palo Alto Panorama, Cradlepoint NetCloud

New service: ingest-api-poller
- Scheduled REST polling per vendor/org
- Webhook receiver for vendor-push events (Meraki, Mist, UniFi all support)
- Rate limit awareness per vendor API limits
- Plugin architecture — VendorAPIPlugin base class
- Credentials in OpenBao, never stored locally
- Normalize all vendor data to common NetPulse schema → NATS

Two modes:
1. Polling — GET vendor API on schedule (60-300s intervals)
2. Webhooks — vendor pushes events to us (near real-time)

MSP consideration: Meraki/Mist have multi-org APIs —
one credential set manages multiple customer orgs.
Critical for MSP use case of NetPulse.

## ChatOps Integration (Phase 4+)

Conversational queries from chat platforms → NetPulse data.

Service: chatops-service
Platforms: Microsoft Teams, Slack, Google Chat, Discord, Mattermost

Query types:
- Device status: "status of router-a"
- Site status: "status of site dallas"  
- Active alerts: "any alerts right now"
- CVE queries: "what CVEs affect firewall-c"
- EOL queries: "when does router-a go end of life"
- Capacity: "what circuits are near capacity"

Architecture:
- Webhook receiver endpoints in Django API (/api/webhooks/{platform}/)
- Intent parser maps natural language to API calls
- Response formatter per platform (Slack Block Kit, Teams Adaptive Cards etc)
- Optional: Claude API integration for richer NLP

Security:
- Map chat user identity to NetPulse RBAC user
- Read-only queries allowed, action commands require approval
- Sensitive data never included in chat responses
- All queries audit logged
- Only respond in approved channels

Proactive alerts pushed to designated channels:
- Critical alerts, CVE notifications, UPS events, capacity warnings

## Web UI Requirements

React SPA with these principles:
- Progressive disclosure — simple first, detail on demand
- Guided onboarding wizard for first-time setup
- Action-oriented — every screen suggests next action
- Empty states that guide users to next step
- Mobile-aware for NOC on-call scenarios
- WebSocket live updates via Django Channels

Key sections: Dashboard, Devices, Telemetry, Configuration,
Logs, Security, CVE, Lifecycle, Flow, Alerts, Reports, Settings

Integration configuration UI:
- Cards for each available integration with Connect button
- Step-by-step setup wizard per integration
- Test connection before saving
- Status indicators for connected integrations

UI Stack: React + Apache ECharts + Tailwind CSS

## Documentation Requirements

Three audiences: Network Engineers, Platform Admins, Contributors
Tool: Docusaurus hosted on GitHub Pages (docs.netpulse.io)

Critical docs to build early:
- Quickstart (up and running in 5 minutes)
- Per-device setup guides with exact CLI commands
  (IOS-XE, IOS-XR, NX-OS, Juniper, Arista)
- Per-integration setup guides (Meraki, Mist, UniFi, Slack, Teams)
- Contributor guides (vendor plugins, TextFSM templates, MIBs)

## Network Topology Mapping (Phase 4)

Auto-generated topology maps from CDP/LLDP with live utilization overlay.

### Three Views
1. Physical Topology — auto-built from CDP/LLDP, links colored by utilization
2. NetFlow Path View — traffic path between src/dst with per-hop latency
3. Site/Geographic View — devices grouped by site, WAN links with utilization

### Link Coloring (utilization)
- Green: 0-60%
- Yellow: 60-80%
- Orange: 80-90%
- Red: 90%+ (needs attention)
- Gray: link down
Link thickness = capacity (1G/10G/40G/100G)

### Technology
- Cytoscape.js (MIT) — topology rendering, large network support
- D3.js (ISC) — NetFlow path visualization
- Both open source, no licensing issues

### Data Model (PostgreSQL)
topology_links table:
  device_a_id, device_b_id (FK to devices)
  interface_a, interface_b
  capacity_gbps, link_type
  discovered_via (cdp/lldp/manual)
  last_seen

### API Response
GET /api/topology/ returns nodes + edges JSON with:
- Node: id, label, type, site, status, risk_score
- Edge: source, target, capacity_gbps, utilization_pct,
        utilization_color, in_bps, out_bps, latency_ms

### Live Updates
WebSocket pushes utilization updates every 30s
InfluxDB queried for latest interface counters
Flow correlator provides per-link latency

### Interactive Features
- Click device → health popup + "view details"
- Click link → utilization chart + latency
- Right-click → run commands, view config/logs/CVEs
- Filter by site, role, device type
- Toggle utilization/alert overlays
- Export as PNG/SVG
- NetFlow path: select src+dst → highlight path with latency

## Network Topology Mapping (Phase 4)

Auto-generated topology maps from CDP/LLDP with live utilization overlay.

### Three Views
1. Physical Topology — auto-built from CDP/LLDP, links colored by utilization
2. NetFlow Path View — traffic path between src/dst with per-hop latency
3. Site/Geographic View — devices grouped by site, WAN links with utilization

### Link Coloring (utilization)
- Green: 0-60%
- Yellow: 60-80%
- Orange: 80-90%
- Red: 90%+ (needs attention)
- Gray: link down
Link thickness = capacity (1G/10G/40G/100G)

### Technology
- Cytoscape.js (MIT) — topology rendering, large network support
- D3.js (ISC) — NetFlow path visualization
- Both open source, no licensing issues

### Data Model (PostgreSQL)
topology_links table:
  device_a_id, device_b_id (FK to devices)
  interface_a, interface_b
  capacity_gbps, link_type
  discovered_via (cdp/lldp/manual)
  last_seen

### API Response
GET /api/topology/ returns nodes + edges JSON with:
- Node: id, label, type, site, status, risk_score
- Edge: source, target, capacity_gbps, utilization_pct,
        utilization_color, in_bps, out_bps, latency_ms

### Live Updates
WebSocket pushes utilization updates every 30s
InfluxDB queried for latest interface counters
Flow correlator provides per-link latency

### Interactive Features
- Click device → health popup + "view details"
- Click link → utilization chart + latency
- Right-click → run commands, view config/logs/CVEs
- Filter by site, role, device type
- Toggle utilization/alert overlays
- Export as PNG/SVG
- NetFlow path: select src+dst → highlight path with latency

## Interface/Circuit Capacity Overrides

Physical interface speed ≠ actual circuit capacity on WAN links.
Must support manual capacity overrides per interface.

### Data Model additions (devices app)
CircuitOverride model:
  device_id (FK)
  interface_name         — "GigabitEthernet0/0/0"
  physical_speed_mbps    — 10000 (10Gbps physical port)
  committed_download_mbps — 500  (actual circuit capacity)
  committed_upload_mbps   — 200  (asymmetric circuits common)
  burst_download_mbps     — 1000 (burst allowed, optional)
  burst_upload_mbps       — 500
  provider               — "AT&T", "Comcast", "Lumen" etc
  circuit_id             — carrier circuit ID for support calls
  monthly_cost           — for budget planning reports
  contract_renewal_date  — alert before expiry
  notes                  — free text

### Utilization Calculation
Without override: (current_bps / interface_speed) * 100
With override:    (current_bps / committed_mbps) * 100

### Topology Map Impact
Link color based on override capacity not physical speed:
  500Mbps circuit at 400Mbps = 80% = orange ⚠️
  NOT: 10Gbps port at 400Mbps = 4% = green ✅ (wrong)

### Bandwidth Planning Impact  
95th percentile trending against committed rate not physical
Budget reports show cost per Mbps, renewal dates, upgrade triggers
Alert when approaching committed rate (configurable threshold)
Alert X days before contract renewal date

### UI
Interface detail page shows:
  Physical speed vs committed capacity
  Current utilization vs BOTH speeds
  Edit override button → form to set capacity
  Provider info, circuit ID, cost, renewal date

Topology map:
  WAN links show committed capacity not physical speed
  Hover tooltip shows: "500/200 Mbps (10G physical)"
  Color based on committed rate utilization

## Availability & Uptime Reporting

Hierarchical availability reporting from organization level 
down to individual interfaces. Not just "was it up" but 
weighted service availability with incident tracking.

### Hierarchy
Organization → Region/Datacenter → Site/Pod → Device → Interface/Circuit

### Models Needed
AvailabilityRecord:
  entity_type (org/region/site/device/interface)
  entity_id
  period_start, period_end
  availability_pct
  total_minutes, outage_minutes, degraded_minutes
  incident_count, mttr_minutes, mtbf_minutes

Incident:
  title, description
  started_at, resolved_at, duration_minutes
  severity (outage/degraded)
  severity_weight (degraded = partial credit)
  affected_entities (M2M)
  root_cause, resolution
  is_maintenance (excluded from SLA calc)

MaintenanceWindow:
  name, start_time, end_time
  affected_devices (M2M), affected_sites (M2M)
  created_by, approved_by
  Alerts suppressed, downtime excluded from SLA

### Key Metrics Per Report
- Availability % (headline)
- Total downtime minutes
- Incident count
- MTTR (mean time to restore)
- MTBF (mean time between failures)
- Longest single outage
- Trend vs previous period

### WAN Circuit SLA Tracking
Track actual vs carrier SLA commitment
Calculate minutes used vs monthly SLA budget
Generate evidence reports for carrier disputes
Credit calculation when SLA breached

### Topology Map Integration
Device color overlay by availability %:
  Green: 99.9%+ | Yellow: 99-99.9% | Orange: 95-99% | Red: <95%

### Report Formats
- Executive summary (org/region level) — PDF exportable
- Drill-down (site/device level) — timeline bar chart
- WAN SLA report — carrier dispute evidence
- Trend report — availability over time

### Maintenance Windows
Planned maintenance excluded from SLA calculations
Alerts suppressed during windows
Still logged for change tracking
Shown on reports as excluded time

## Business Service Availability (Phase 5+)

Map infrastructure components to business services and calculate
service health based on component health and dependency relationships.

### The Concept
Instead of "Router-A is down" → "E-Commerce checkout is degraded"
Translates infrastructure events into business impact automatically.

### Example Service Definition
E-Commerce Platform:
  Load Balancers (require 1 of 2):
  ├── LB-DC1 (Datacenter-1)
  └── LB-DC2 (Datacenter-2)
  
  App Servers (require 3 of 5 per DC):
  ├── App-DC1-01 through App-DC1-05
  └── App-DC2-01 through App-DC2-05
  
  Database (require primary + 1 replica):
  ├── DB-Primary
  ├── DB-Replica-1
  └── DB-Replica-2
  
  Network Path (all required):
  ├── WAN-Edge-DC1
  ├── Core-SW-DC1
  ├── WAN-Edge-DC2
  └── Core-SW-DC2

### Service Health Calculation
Each component group has a threshold:

  Load Balancers: 1 of 2 required
    2/2 healthy → GREEN
    1/2 healthy → YELLOW (degraded, no redundancy)
    0/2 healthy → RED (service down)

  App Servers DC1: 3 of 5 required  
    5/5 healthy → GREEN
    3-4/5 healthy → YELLOW (degraded capacity)
    <3/5 healthy → RED (insufficient capacity)

  Overall service = worst component group status

### Models Needed
BusinessService:
  name, description
  owner, team
  sla_target_pct
  status (green/yellow/red)

ServiceComponent:
  service (FK)
  name, component_type (network/server/database/storage)
  device_id or server_id (FK)
  required_count    — minimum needed for service
  total_count       — total in group
  weight            — impact weight on overall service

ServiceDependency:
  service (FK)
  depends_on_service (FK — service to service dependencies)
  dependency_type (hard/soft)
  — hard: if dependency fails, service fails
  — soft: if dependency fails, service degrades

### Dependency Chain Example
E-Commerce → Payment Service → Banking API (external)
  If Banking API degrades → Payment Service yellow
  → E-Commerce yellow (soft dependency)

### Service Map View (UI)
Visual dependency map showing:
  Business services as nodes
  Dependencies as edges
  Color = current health status
  Click service → component breakdown
  Click component → device/server details

### Integration Points
  Network devices → existing NetPulse inventory
  Servers → agent-based or API monitoring
  External services → synthetic probes / API health checks
  Cloud services → AWS/Azure/GCP health APIs

### Availability Reports Extended
Service availability report adds:
  Service-level availability % (not just device)
  Business impact of each incident
    "Core-SW-1 failure caused E-Commerce degradation
     for 8 minutes affecting 3 business services"
  SLA tracking at service level
  Executive dashboard shows service health not device health

### Agent Strategy for Servers
Options for server health data:
  1. SNMP — works, limited data
  2. Node Exporter → OTLP → ingest-otlp (preferred)
  3. Telegraf agent → InfluxDB line protocol → ingest-influx
  4. Cloud provider APIs (EC2, Azure VM health)
  5. Synthetic probes — HTTP health check endpoints

### Future Phase — AIOps
  Correlate infrastructure events with service degradation
  Learn normal dependency behavior
  Predict service impact before it happens
  "Core-SW-1 CPU at 95% — E-Commerce likely to degrade in 15min"

## TV/NOC Display Mode (Phase 4)

Browser-based TV dashboard mode — no native app needed.
Works on any smart TV, Chromecast, or Raspberry Pi kiosk.

Route: /tv (full screen, no navigation chrome)
Auth: Read-only PIN/token (safe for always-on displays)

Features:
- Auto-rotating dashboard views (configurable interval)
- Large text readable from across NOC floor
- High contrast color scheme
- WebSocket live updates
- Configurable layout per display URL params
- No mouse interaction required

Rotating views:
1. Network health map (sites colored by status)
2. Active alerts list
3. Top bandwidth circuits
4. Business service status board
5. Recent incidents timeline
6. WAN circuit utilization heatmap

Deployment options (document all in docs/):
1. Smart TV browser — navigate to /tv URL
2. Chromecast — cast from laptop
3. Raspberry Pi kiosk — Chromium in kiosk mode (recommended for permanent NOC)
4. Android TV / Fire TV — browser app

Do NOT build native Roku/Apple TV apps —
browser-based approach covers all use cases
with far less development overhead.

## Distributed Remote Poller Architecture

For large environments, single central poller won't scale.
Need distributed polling nodes that can be deployed close
to what they monitor.

### Problem at Scale
Single central poller limitations:
├── SNMP polling 10,000 devices from one host = poll storms
├── WAN latency affects SNMP response times / timeouts
├── Single point of failure for all polling
├── Firewall rules required from central to every device
└── gNMI dial-out works but SNMP/SSH still needs reach

### Poller Node Architecture
Central NetPulse Platform
│
│ mTLS (outbound from poller only)
│
┌───────┴────────────────────────────┐
│                                    │
▼                                    ▼
Poller-DC1                      Poller-Branch
├── Polls local devices          ├── Polls local devices
├── Receives local traps         ├── Receives local syslog
├── Receives local syslog        ├── Local disk buffer
├── Local disk buffer            └── Forwards to central
└── Forwards to central              over single mTLS conn
Each poller:
├── Deployed as Docker container or systemd service
├── Runs all ingest services locally
├── Single outbound mTLS connection to central
├── No inbound firewall rules needed
├── Local buffer if central unreachable
└── Registered and managed from central UI

### Poller Assignment
Devices assigned to pollers:
├── Auto-assign by subnet (devices in 10.1.0.0/16 → Poller-DC1)
├── Manual override per device
├── Failover — if primary poller unreachable, secondary takes over
└── Load balancing — distribute devices across multiple pollers
Central platform:
├── Knows which poller owns which device
├── Routes commands to correct poller
│   (run show command → sent to owning poller → executes → returns)
├── Monitors poller health (heartbeat)
└── Alerts if poller goes silent

### Poller Registration & Security
New poller deployment:

Admin generates registration token in UI
Poller starts with token
Central issues mTLS certificate via OpenBao PKI
Token invalidated after use (one-time)
Poller authenticates with certificate going forward
Unique identity per poller — compromise of one
doesn't affect others

Certificate rotation:
└── Automatic via OpenBao PKI — pollers rotate certs
before expiry without admin intervention

### Poller Health Monitoring
Central tracks per poller:
├── Last heartbeat timestamp
├── Devices assigned vs actively polling
├── Poll success rate (% of polls getting responses)
├── Queue depth (messages buffered locally)
├── Version (alert if poller needs update)
├── Resource usage (CPU, memory, disk for buffer)
└── Network latency to central
Alerts:
├── Poller heartbeat missed → immediate alert
├── Poll success rate drops → investigate devices
├── Buffer growing → central connection issues
└── Version mismatch → update needed

### Deployment Sizing Guide
Small poller (branch office):
50-200 devices
2 CPU, 4GB RAM, 20GB disk (buffer)
Docker container on existing server or small VM
Medium poller (regional DC):
200-1000 devices
4 CPU, 8GB RAM, 50GB disk
Dedicated VM or small server
Large poller (large DC / dense environment):
1000-5000 devices
8 CPU, 16GB RAM, 100GB disk
Dedicated server
Very large environments:
Multiple pollers per DC
Load balanced across poller pool
Same mTLS architecture, just more nodes

### Poller vs Collector Distinction
NetPulse Collector (already documented):
Purpose: forward device-initiated telemetry to cloud
Devices push TO collector (gNMI dial-out, syslog, traps)
Collector forwards to cloud NetPulse
Remote Poller (this feature):
Purpose: poll devices that don't support push
Poller reaches OUT to devices (SNMP, SSH, NETCONF)
Results forwarded to central NetPulse
In practice:
Full poller node = Collector + Poller combined
Handles both push (receive) and pull (initiate)
Single deployment covers all cases

### UI Management
Settings → Pollers page:
├── List all registered pollers
│   name, location, status, device count, last seen
├── Register new poller (generate token)
├── View poller health details
├── Reassign devices between pollers
├── Decommission poller (reassign devices first)
└── Force poller update
Device detail page:
└── Shows assigned poller
"Monitored by: Poller-DC1 (healthy)"
Override button to reassign

## Frontend Stack Details
- React with TypeScript — all component files use .tsx extension
- Vite as build tool (fast, modern, excellent TypeScript support)
- Tailwind CSS for styling
- Apache ECharts for charts and graphs
- Cytoscape.js for network topology maps
- D3.js for NetFlow path visualization
- React Query for API data fetching and caching
- Zustand for global state management
- React Router for navigation

## Data Persistence Strategy

Development: Named Docker volumes (current)
Production: Bind mounts to ${DATA_DIR:-/opt/netpulse/data}/

Production docker-compose.yml should use:
  - ${DATA_DIR}/postgres:/var/lib/postgresql/data
  - ${DATA_DIR}/influxdb:/var/lib/influxdb2
  - ${DATA_DIR}/opensearch:/usr/share/opensearch/data
  - ${DATA_DIR}/valkey:/data
  - ${DATA_DIR}/nats:/data
  - ${DATA_DIR}/openbao:/openbao/data

Add DATA_DIR=/opt/netpulse/data to .env for production

Backup strategy:
  docker compose stop → tar DATA_DIR → docker compose start
  OpenBao data is most critical — back up separately
  Document restore procedure in docs/deployment/backup-restore.md

Keep all databases IN Docker — not external.
Bind mounts give full data accessibility without complexity.


# NetPulse — Claude Code Context

NetPulse is a push-first, open source network intelligence platform.
Full architecture in docs/ARCHITECTURE.md.

## Current Status
- Docker Compose scaffold: 22 services ✅
- Infrastructure (postgres, influxdb, opensearch, valkey, nats, openbao): running ✅
- Django 6.0 backend (services/api): 9 apps, models, REST API, JWT auth, RBAC ✅
- services/ingest-grpc: gNMI dial-out receiver (27 tests) ✅
- services/ingest-syslog: RFC 3164/5424 receiver (52 tests) ✅
- services/ingest-snmp: SNMP poller + trap receiver (38 tests) ✅
- services/ingest-flow: NetFlow/sFlow + latency correlation (41 tests) ✅
- services/ingest-otlp: OpenTelemetry receiver (86 tests) ✅
- services/ingest-api-poller: Meraki/Mist/UniFi plugin system (36 tests) ✅
- services/stream-processor: NATS consumer, anomaly detection (91 tests) ✅
- services/frontend: React + TypeScript + Vite + Tailwind ✅
- services/api check-engine: agentless service checks — http/https/tcp/icmp/dns/tls/smtp/ssh_banner ✅
- reachability-monitor: TCP/22 device liveness + status transitions ✅
- apps/alerting: teams, escalation policies, alert-route matching + email (Stage 1) ✅
- Platforms: ios_xe/ios verified on real C8000V; ios_xr/nxos/junos/eos/fortios/panos config-generated ✅
- FortiOS interface discovery: platform-aware ("get system interface" + custom parser, LLDP best-effort) ✅
- SNMPv3 authPriv config generation per platform (IOS-XE/NX-OS/Junos/EOS/FortiOS); v2c plaintext warning ✅
- gNMI memory/CPU field mapping for Cisco IOS-XE subscriptions ✅
- Topology dedup: canonical link ordering + 4-field UniqueConstraint (discovery from both ends = 1 link) ✅
- Alert auto-resolution: state-driven, label-matched recovery + 90-day purge ✅
- Maintenance windows: suppress alerts during scheduled maintenance (one-off + daily/weekly/monthly) ✅
- Notification channels: per-team email + Slack + Discord webhooks ✅
- Default/system alert rules: seed_alert_rules seeds the six rules the engines
  emit (is_system, protected from deletion); disabling a rule now suppresses its
  alerts (stream-processor + interface_monitor honor is_active) ✅
- Auth rate limiting: JWT token/refresh endpoints throttled (H1 security fix) ✅
- Admin user management: AdminOnly /api/users/ CRUD (role↔Group sync) + live
  Settings → Users page; delete guards (no self-delete, no deleting/demoting/
  deactivating the last admin) ✅
- Dashboard: infrastructure health, empty states, live WebSocket, service-check + cert-expiry widgets ✅
- Onboarding wizard: 4 steps, integrations selection ✅
- HTTPS enforced: nginx redirects HTTP :3000 → HTTPS :3443 (self-signed bootstrap) ✅
- Dependabot: weekly pip/npm/docker/actions updates, grouped (Django/React/Tailwind) ✅
- Discovery page wiring: /api/devices/discovery/{jobs,discovered}/ — job CRUD +
  discovered-device approve (creates active Device)/reject; live Settings →
  Discovery page with subnet scope + OT/ICS exclusions and pending-approval queue ✅
- Collection-method display: /api/devices/{id}/collection-status/ +
  device-header badges / Telemetry-tab bar (📡 gNMI / 📊 SNMP / ⚠️ none) ✅
- gNMI/SNMP adaptive polling: ingest-grpc stamps a Valkey gNMI heartbeat
  (gnmi:last_seen:{id}, TTL 180s); while gNMI is active ingest-snmp polls ONLY
  the essential system OIDs (ALWAYS_POLL_OIDS: sysUpTime/sysDescr/sysName/
  sysLocation — gNMI doesn't carry uptime) and suppresses the rest, auto-resuming
  the full poll when the stream stalls; collection-status reports snmp.suppressed ✅
- SNMPv3 interface discovery: approved v3 devices auto-add LLDP-connected
  interfaces (build_snmp_auth shared by enrich + discovery; one SnmpEngine per
  walk for v3 engine-ID discovery); enrich republishes to the poller ✅
- FortiOS detection from sysDescr (FortiOS/FortiGate/Fortinet) + bulk-approve of
  unknown-platform devices when the vendor is known (vendor default platform;
  cisco → operator picks) ✅
- FortiGate SNMP CPU/memory surfaced (fgSysCpuUsage/MemUsage/MemCapacity →
  metrics FIELD_MAP; direct memory_used_pct honored) ✅
- Ping latency: reachability-monitor stores TCP RTT in InfluxDB
  (device_reachability); GET /api/devices/{id}/reachability/; Telemetry latency
  chart, Overview Ping tile, dashboard latency sparklines; latency alert rules ✅
- Reachability liveness probes TCP/22 then TCP/443 fallback (firewalls blocking
  SSH from the collector still register as live) ✅
- Config collection: initial baseline on approval (enrich step 4) + twice-daily
  scheduled run (config-manager at CONFIG_COLLECTION_HOUR_1/_2, default 07:00 &
  19:00 UTC) with change detection + "Config Changed" alert ✅
- Team notification targets: TeamMember notify_email/slack/discord + user profile
  slack_user_id/discord_user_id; get_team_notification_targets; member CRUD UI ✅
- Hostname display: STRIP_DOMAIN_FROM_HOSTNAMES + DOMAIN_SUFFIX, SystemSetting
  override, Device.display_hostname, Settings → General; full hostname kept for
  SSH/SNMP/syslog ✅
- Topology: interface names canonicalised (Gi3 → GigabitEthernet3) so LLDP links
  don't duplicate across SNMP/SSH; stale-port cleanup; data migration 0015 ✅
- MIB support: mibs/ tree (standard/vendor/community/custom) mounted into api +
  ingest-snmp; apps.mibs parser/index + /api/mibs/ (list/upload/delete/resolve);
  validate_mib/list_mibs commands; Settings → MIB Files; scripts/download_mibs.sh ✅
- Platforms: added sonicwall (SonicOS), aos_cx (HPE AOS-CX), aruba (Aruba AOS) —
  PLATFORM_DEVICE_OIDS, FIELD_MAP, sysDescr/banner detection, choices migration ✅
- AOS-CX detection: SSHDetect aruba_aoscx→aos_cx; sysDescr "HPE ANW"/"HPE Aruba"
  →aos_cx (not just "ArubaOS-CX"); sysObjectID enterprise 47196→aruba/aos_cx ✅
- AOS-CX SNMP enrichment: model from sysDescr ("HPE ANW R9Y04A 6100 … Sw" →
  "R9Y04A 6100 48G CL4 4SFP+ Sw"), os_version from trailing firmware token
  (PL.x), serial via entPhysicalSerialNum WALK (chassis at vendor index, not .1) ✅
- AOS-CX environment telemetry (SNMP-only; REST not on 6100): walk-based CPU
  (hrProcessorLoad avg), memory % (hrStorage idx 1), temperature (ENTITY-SENSOR
  with scale/precision), fan/PSU presence (entPhysicalClass). New InfluxDB
  measurement device_environment + telemetry scalars (cpu_pct, memory_used_pct,
  memory_*_bytes, temp_max_c, fan_count, psu_count). Poller WALK support
  (walk_oids in device payload). Temperature alert rules seeded (warning ≥75°C,
  critical ≥85°C, sensor-failed) ✅
- SNMPv3 reliability: poller now uses a fresh SnmpEngine per poll (avoids any
  stale engineBoots/engineTime on a long-lived shared engine — general
  robustness). NOTE: the AOS-CX "Wrong SNMP PDU digest" seen in the lab was NOT
  the engine — it was traced to a WRONG SNMPv3 auth/priv key stored in OpenBao
  for the credential profile (the device uses a different passphrase). Correct
  keys → both pysnmp 6.3 and 7.1 succeed. Fix = update the credential's SNMPv3
  keys in Settings → Credentials ✅
- manage.py test now delegates to pytest (config/test_runner.py) ✅
- Environment detail: per-fan RPM + per-PSU watts/status (ENTITY-SENSOR
  entPhySensorOperStatus — the 6100 DOES expose per-unit status; RPM reads -1 =
  unavailable, PSU watts 0) + PoE budget/usage (POWER-ETHERNET-MIB
  pethMainPseTable WALK; raw OIDs, MIB not in collection; AOS-CX reports budget
  at 2× = half-watts, 740→370W; 56W used ≈15%). Stored per-unit in
  device_environment; /api/devices/{id}/metrics/ returns fans/psus/poe; UI shows
  status dots + PoE bar (green<50/amber50-80/red>80) ✅
- ARP/MAC collection: apps/arp_mac collects ARP + MAC tables over SSH (Netmiko +
  ntc-templates 9.1.0, which already ships aruba_aoscx templates; FortiOS and
  SonicWall ARP-only — firewalls have no MAC address-table). Models
  ARPEntry/MACEntry/MACVendor (relational). Endpoints
  /api/devices/{id}/arp/|/mac/|/arp-mac/collect/, /api/network/search/ (find
  host by IP/MAC), /api/network/mac-vendor/{mac}/. UI: device ARP/MAC tab +
  global IP/MAC search on Devices page ✅
  - SonicWall ARP: no Netmiko SonicOS driver, and the SonicOS login banner
    interrupts Netmiko's generic-driver auth (the banner prints BEFORE the
    `Password:` prompt). Collected over a DIRECT paramiko SSH shell instead
    (`_collect_sonicwall_arp` → `_drive_sonicwall_shell`): connect with
    `banner_timeout=30`/`auth_timeout=30`/`look_for_keys=False`/
    `allow_agent=False`, `invoke_shell()`, then drive the shell:
    (1) read the banner; (2) DOUBLE PASSWORD — SonicWall re-prompts for the
    password on the interactive shell even after paramiko has authenticated the
    SSH session (the banner ends with `Access denied\nPassword:`), so
    `_drive_sonicwall_shell` re-sends the SAME password when it sees that prompt
    (normal SonicOS behavior; both prompts take the same password); (3) send
    `no cli pager session` as its OWN command and drain its response to disable
    paging; (4) send `show arp caches` and read the full reply. With paging
    disabled the whole table comes back unpaged (~69K chars in the lab) — no
    `--More--` handling needed. More reliable than the Netmiko generic driver
    for SonicWall. Custom TextFSM template
    apps/collectors/templates/sonicwall_show_arp_caches.textfsm parses the
    IP/Type/MAC/Vendor/Interface/Timeout columns (vendor may contain spaces →
    `\s{2,}` delimiter before the X0:Vnnn interface); collector
    `_parse_sonicwall_arp` maps "Expires in N minutes" → age_minutes,
    "Permanent published" → None, and Static/Dynamic TYPE → ARPEntry.entry_type
    (`_arp_entry_type`; static/permanent → static, else dynamic; other platforms
    populate it from a flags/state column where present — Cisco's encapsulation
    "type"/ARPA is excluded). Device-reported Vendor dropped (API derives it from
    the MAC OUI).
- Ping latency on device list: GET /api/devices/ping-summary/ (per-device
  current/avg/max RTT + 24h uptime% + ~24-pt sparkline from device_reachability,
  cached 60s); Ping column with colored ms + inline SVG sparkline, fetched in
  background ✅
- Reachability stale-UI fix: monitor broadcasts every device's state on its
  first cycle after restart (not just transitions), so open UIs refresh ✅
- OpenBao token resolution is lazy in ingest-snmp (re-read on demand + self-heal
  on auth failure) — fixes "secrets empty after restart" reboot race ✅
- Scheduler: run_scheduler is the SINGLE/authoritative scheduler (Celery is in
  requirements but UNUSED — no tasks/beat schedule). Compose `scheduler` service
  runs `python manage.py run_scheduler` + mounts openbao-data:ro (for SSH creds).
  Startup (idempotent): seed alert rules, unseal OpenBao, load OUI registry if
  empty. Periodic: alert purge (daily), ARP/MAC collection (every 6h,
  ARP_MAC_COLLECT_INTERVAL_S), MAC-vendor OUI refresh (weekly,
  MAC_VENDOR_UPDATE_INTERVAL_S) — 6h/weekly first fire one interval after start ✅
- Backend tests: 996 passing (services/api); ingest-snmp 58; ingest-grpc 32 ✅

## Lab devices (current)
Remote lab host: `azadmin@wco2lnxnetmon01`. (Credentials live in `.env`/OpenBao —
never documented here per the security rules.)
- wco2-idf5-asw-01: id=2, 10.150.0.21, aos_cx (HPE AOS-CX 6100). SNMPv3 user
  `fpsrw`, authPriv SHA/AES (keys in OpenBao). Aruba Central-managed
  (device-prod-d2.central.arubanetworks.com). Verified: model "R9Y04A 6100 48G
  CL4 4SFP+ Sw", os_version PL.10.16.1030, serial TW45LHP009 (entPhysicalSerialNum
  index 112001). CPU ~22%, memory ~29%, 1 temp sensor ~29°C, 4 fans, 1 PSU.
- wco2-idf6-asw-01: 10.150.0.25, aos_cx (same model/credentials) — not yet in
  inventory.

### AOS-CX (HPE 6100) — verified SNMP findings
- sysDescr: `HPE ANW {model} {firmware}` e.g. "HPE ANW R9Y04A 6100 48G CL4 4SFP+
  Sw PL.10.16.1030"; sysObjectID `1.3.6.1.4.1.47196.4.1.1.1.260` (enterprise 47196
  = HPE Networking).
- CPU: `hrProcessorLoad` (1.3.6.1.2.1.25.3.3.1.2) at vendor indexes (196608/…),
  NOT .1 — must WALK and average.
- Memory: `hrStorage` index 1 = "Physical memory" (GET .5.1/.6.1/.4.1 works).
- Temperature: ENTITY-SENSOR-MIB (1.3.6.1.2.1.99.1.1.1.*) — one celsius sensor
  (type=8); value×10^scale×10^-precision (e.g. 28875 milli → 28.875°C).
- Fan/PSU LIMITS on the 6100: per-unit RPM is unavailable (rpm sensors read -1)
  and there is no standard per-unit oper-status (the entPhysical `.8` column is
  entPhysicalHardwareRev, NOT a status). We report fan/PSU PRESENCE/COUNT via
  entPhysicalClass (fan=7, psu=6) + names; reliable status only for sensors
  (entPhySensorOperStatus). Higher-end models (8xxx) may expose more.
- REST API: NOT supported on the 6100 (login 400/401) — SNMP only. Higher-end
  models may support it.
- SSH banner is generic OpenSSH (no platform hint) — detection relies on
  SSHDetect / sysDescr / sysObjectID.
- SNMPv3 "Wrong PDU digest": root cause is a wrong SNMPv3 auth/priv key stored
  in OpenBao for the credential profile (device uses a different passphrase),
  NOT pysnmp/engine — confirmed by api (7.1) + ingest (6.3) both failing on the
  stored key and both succeeding on the correct key. Fix in Settings →
  Credentials. (The poller also moved to a fresh engine per poll for general
  robustness.)

## In Progress
- SSO authentication — Stage 1 (Google OAuth2 backend; apps/sso + SSOProvider).
  See "SSO / Single Sign-On" below.

## Technology Stack
- Backend: Python 3.13, Django 6.0, DRF, Django Channels
- Frontend: React, TypeScript (.tsx), Vite, Tailwind CSS
- Charts: Apache ECharts
- Topology: Cytoscape.js
- Flow viz: D3.js
- State: Zustand
- Data fetching: React Query
- Auth: JWT (djangorestframework-simplejwt); SSO via social-auth-app-django
  (Google/Azure AD/Okta/GitHub OAuth2) minting the same JWT
- Database: PostgreSQL 17 + JSONB
- Time-series: InfluxDB OSS
- Search/logs: OpenSearch
- Cache/WS broker: Valkey
- Message bus: NATS + JetStream
- Secrets: OpenBao (Vault-compatible, dev mode)
- Deployment: Docker Compose (on-prem), Helm (cloud)

## Architecture Principles
- Push-first telemetry, SNMP polling as fallback
- Security first — OpenBao for ALL credentials
- Never store plaintext credentials anywhere
- vault_path in PostgreSQL, actual secrets in OpenBao only
- Always show "🔒 Stored securely in OpenBao" in credential UI
- Least privilege per microservice
- Multi-tenant from day one

## Django Apps (services/api/apps/)
Model names below reflect what is actually defined in each app's models.py.
- core: base TimestampedModel, health endpoints, system settings, shared utilities
- devices: Device (+ unreachable_since), Site, DeviceGroup, TopologyLink, DiscoveryJob, DiscoveredDevice
- credentials: CredentialProfile (multi-protocol; secrets in OpenBao)
- telemetry: TelemetryConfig, MonitoredInterface
- compliance: CompliancePolicy, CompliancePolicyRule, ComplianceResult
- alerts: AlertRule, AlertEvent, AlertChannel
- cve: CVE, DeviceCVE, CVEFeedSettings
- lifecycle: LifecycleMilestone
- security: DeviceRiskScore
- collectors: Collector
- configbackup: ConfigBackupSettings, DeviceConfig
- integrations: NetBox/DNA import endpoints (no persistent models)
- logs: OpenSearch-backed log query (no DB model)
- tls: SSL/TLS + CA certificate management
- checks: ServiceCheck, CheckResult (agentless synthetic monitoring; types
  http/https/tcp/icmp/dns/tls/smtp/ssh_banner; engine = run_check_engine)
- alerting: Team, TeamMember, ContactMethod, EscalationPolicy, EscalationStep,
  AlertRoute, AlertNotification (Stage 1: route matching + email; on-call +
  acknowledgement are Stage 2)
- sso: SSOProvider (external-IdP login config; client_secret in OpenBao, not the
  DB). Stage 1 = Google OAuth2. See "SSO / Single Sign-On" below.
- arp_mac: ARPEntry, MACEntry, MACVendor — ARP/MAC tables collected over SSH
  (Netmiko + ntc-templates), OUI vendor lookup, find-device-by-IP/MAC. Collected
  every 6h by run_scheduler; manual via collect_arp_mac or the per-device
  arp-mac/collect endpoint.

## Service Check Types & Libraries (apps/checks/runner.py)
- http/https → aiohttp; tcp → asyncio.open_connection; icmp → icmplib
  (needs NET_RAW; check-engine sets cap_add + ping_group_range); dns → aiodns;
  tls → stdlib ssl (days_remaining/cert_cn/issuer); smtp → aiosmtplib (connect
  + EHLO); ssh_banner → asyncio TCP banner read.

## gNMI field mappings (Cisco IOS-XE, confirmed)
- Interface counters arrive as `<InterfaceName>/<leaf>` (e.g.
  GigabitEthernet1/in_octets) → interface_stats (tagged if_index + if_name).
- memory-statistics: Processor/used_memory → memory_used_bytes,
  Processor/free_memory → memory_free_bytes, Processor/total_memory →
  memory_total_bytes.
- cpu-utilization: five_seconds → cpu_5sec_pct, one_minute → cpu_1min_pct,
  five_minutes → cpu_5min_pct.

## Platform Support
ios_xe, ios (verified on a real Cisco C8000V), ios_xr, nxos, junos, eos,
fortios, panos. FortiOS has no gNMI — uses SNMP (Fortinet enterprise OIDs
fgSysCpuUsage 1.3.6.1.4.1.12356.101.4.1.3.0 / fgSysMemUsage .4.1.4.0 /
fgSysMemCapacity .4.1.5.0) + Syslog + NetFlow; SSH-banner auto-detection covers
FortiOS/PAN-OS that Netmiko SSHDetect misses.

### SonicWall (SonicOS) notes
- REST API is preferred over SSH for config backup AND enrichment.
  - Auth: RFC-7616 HTTP Digest (SHA-256) — `requests.auth.HTTPDigestAuth`.
    Basic auth is disabled by default in SonicOSX 8.
    POST `/api/sonicos/auth` body `{"override": true}` → `status.info[0]`
    (`auth_code == "API_AUTH_SUCCESS"`, model, privilege).
  - Config: GET `/api/sonicos/config/current` → top-level `model`,
    `serial_number`, `firmware_version`, `system_uptime`, plus the full JSON
    config; `administration.firewall_name` is the hostname.
  - TLS: `verify=False` (device cert is self-signed). GOTCHA: the api image sets
    `REQUESTS_CA_BUNDLE`, and requests' env-merge turns a per-request
    `verify=None` into that bundle BEFORE `session.verify=False` applies — so
    `SonicWallClient` sets `session.trust_env=False` AND passes `verify=` on
    every call. Just setting `session.verify=False` is silently ignored.
  - Credentials: prefers the HTTPS/API profile credential (`https_username` +
    `https_password`, `https_port`), falls back to SSH (`resolve_rest_credentials`).
  - Sessions are limited — always `logout()` (DELETE `/api/sonicos/auth`); the
    client context manager does. Client: `apps/compliance/sonicwall_client.py`.
  - Verified live on the lab NSv (SonicOSX 8.2.1-8010): model "NSv XS",
    serial 0017-C5F1-0547, ~1.5 MB config.
- SNMP fallback (when REST is unavailable): SNMPv3, enterprise OID
  1.3.6.1.4.1.8741; sysDescr "SonicWALL {model} ({os_details})" parsed for
  model/os_version (enrich `_parse_sonicwall_descr`); serial from
  `snwlSysSerialNumber` 1.3.6.1.4.1.8741.1.3.1.1.0 when entPhysicalSerialNum is
  empty. Netmiko has no SonicOS driver → device_type `generic` (sonic_os is not
  a valid Netmiko type).
- SonicWall SNMP CPU/memory OID versions (the CPU/mem subtree MOVED between
  major SonicOS releases — poll BOTH, use whichever returns non-zero):
  - v7 (SonicOS 7.x): legacy `1.3.6.1.4.1.8741.1.3.2.*`
    CPU `…8741.1.3.2.3.0` (%), MemUsed `…8741.1.3.2.2.0` (KB),
    MemTotal `…8741.1.3.2.1.0` (KB) → memory_used_pct = used/total×100.
    Confirmed live: v7 TZ 670 (SonicOS 7.3.2) responds to 1.3.2.x, NOT 1.3.1.x.
  - v8 (SonicOSX 8.x): `1.3.6.1.4.1.8741.1.3.1.*`
    CPU `…8741.1.3.1.3.0` (%), Memory `…8741.1.3.1.4.0` (% direct, no calc),
    Conns `…8741.1.3.1.2.0`.
    Confirmed live: v8 NSv XS (SonicOSX 8.2.1) responds to 1.3.1.x.
  - Implementation: PLATFORM_DEVICE_OIDS["sonicwall"] (snmp_publish) polls both
    subtrees; FIELD_MAP maps 1.3.1.x to cpu_pct/memory_used_pct/connections and
    1.3.2.x to cpu_pct_alt/memory_used_kb/memory_total_kb; query_device_metrics
    prefers the primary (1.3.1.x) and falls back to the legacy values
    (cpu_pct_alt; memory_used_pct derived from the KB pair) when the primary is
    missing or zero.
- Docker containers must MASQUERADE-NAT to the host IP (SonicWall restricts
  mgmt by source IP) — see "Docker NAT (Required)".

### FortiOS notes
- Config collection uses `show full-configuration` (FortiOS has no
  `show running-config`); the `#config-version` / `#conf_file_ver` / `#buildno`
  / `#global_vdom` header lines are stripped before change-detection hashing
  (they drift per session and `#config-version` embeds the running user).
- Expected/benign: every NetPulse config-collection SSH session makes the
  Netmiko fortinet driver disable paging, which FortiOS logs as a
  `cfgpath=system.console` config event. The syslog normalizer tags these
  `fortios_benign=true` (severity floored to info) — they are NOT real config
  changes. FortiOS devices will show frequent SSH sessions from the collector;
  this is normal.
- SNMP needs a valid FortiOS license. On unlicensed/eval VMs the SNMP daemon
  can't read `vm.lic` and FortiOS emits `Secure Module Access Violation`
  (`secappdomain=SNMPD`); the normalizer tags these `fortios_license_warning`
  with an explanatory note.

## Credential System (in progress)
CredentialProfile model:
  name, credential_type, description, vault_path
  username, auth_method, snmp_version, snmp_security_level
  auth_protocol, priv_protocol, port, tls_enabled
  created_by, last_updated, last_tested, last_test_result

  Types: snmpv1, snmpv2c, snmpv3, ssh_password, ssh_key,
         http_basic, http_token, http_apikey, gnmi, netconf

DeviceCredential (through model):
  device (FK), credential (FK), purpose, is_primary
  last_used, last_success, failure_count, notes

  Purposes: snmp_polling, ssh_config, ssh_backup,
            netconf, gnmi, http_api

## Settings Navigation (in progress)
/settings/general        — platform config
/settings/users          — users + roles (RBAC)
/settings/credentials    — credential profiles
/settings/integrations   — Meraki/Mist/UniFi/Slack/Teams/PagerDuty
/settings/alerting       — rules, maintenance windows, templates
/settings/discovery      — jobs, subnets, OT exclusions
/settings/collectors     — registered collectors/pollers
/settings/data-sources   — CVE feeds, EOL sources
/settings/system         — backup, audit log, about

## Frontend Routes
/                → redirect to /dashboard
/login           → JWT login page
/dashboard       → main dashboard
/devices         → device inventory
/devices/:id     → device detail
/sites           → sites/locations
/topology        → network topology map
/configs/compare → config diff/compare
/alerts          → alert list
/logs            → fleet syslog viewer
/checks          → service checks (agentless synthetic monitoring) + history panels
/settings/alert-routing → teams, escalation policies, alert routes (Stage 1)
/cve             → CVE exposure
/lifecycle       → EOL management
/settings/*      → settings sub-pages (see above)

## API Endpoints
/api/health/                    — platform health
/api/health/infrastructure/     — service connectivity check
/api/auth/token/                — JWT obtain
/api/auth/token/refresh/        — JWT refresh
/api/sso/providers/             — GET public (enabled providers for login buttons); admin CRUD
/api/sso/providers/{id}/        — admin GET/PUT/DELETE one provider
/api/sso/providers/{id}/test/   — admin: validate provider config
/auth/complete/{backend}/       — OAuth callback (social_django); success → redirect to frontend with JWT
/api/users/                     — admin user management CRUD (AdminOnly; delete/demote guards)
/api/users/me/                  — current user profile + preferences
/api/devices/                   — device CRUD (sortable: ?ordering=, filter: ?site=&status=)
/api/devices/topology/          — LLDP topology nodes + edges
/api/devices/test-connection/   — test device connectivity
/api/devices/:id/metrics/       — InfluxDB snapshot + series + lldp_neighbors + environment
/api/devices/:id/poll-now/      — trigger an immediate SNMP poll
/api/devices/:id/interfaces/    — monitored interfaces (GET/POST replace selection)
/api/devices/:id/interfaces/discover/ — SNMP/SSH interface + LLDP discovery
/api/devices/:id/topology/discover/   — LLDP neighbour discovery → TopologyLink
/api/devices/discovery/jobs/    — discovery job CRUD (+ /:id/discovered/)
/api/devices/discovery/discovered/    — discovered devices (+ /:id/approve/ | /reject/)
/api/credentials/               — credential profile CRUD
/api/credentials/:id/test/      — test credential against IP
/api/sites/                     — site CRUD (+ /:id/devices/)
/api/alerts/                    — alert rules/events/channels
/api/logs/                      — OpenSearch log query (filters incl. from/to on @timestamp)
/api/checks/                    — service check CRUD
/api/checks/:id/run-now/        — probe a check immediately
/api/checks/:id/results/        — check result history + uptime summary (?period=1h|6h|24h|7d)
/api/checks/summary/            — up/down/degraded/unknown counts
/api/alerting/teams/            — teams (+ :id/members/ add/remove)
/api/alerting/policies/         — escalation policies (+ :id/steps/)
/api/alerting/routes/           — alert routes (+ /test/ to match a sample alert)
/api/alerting/notifications/    — notification delivery history (read-only)
/api/cve/                       — CVE data
/api/lifecycle/                 — EOL data
/api/devices/ping-summary/      — per-device ping current/avg/max/uptime + 24h sparkline (cached 60s)
/api/devices/{id}/arp/          — device ARP table (?search)
/api/devices/{id}/mac/          — device MAC table (?vlan&interface&search)
/api/devices/{id}/arp-mac/collect/ — POST: collect ARP/MAC now over SSH
/api/network/search/?q=         — find which device sees an IP/MAC
/api/network/mac-vendor/{mac}/  — OUI → vendor lookup
/ws/telemetry/                  — WebSocket live metrics
/ws/alerts/                     — WebSocket live alerts
/ws/devices/                    — WebSocket device reachability updates

## Key Management Commands
python manage.py run_stream_processor
python manage.py run_config_manager
python manage.py run_alert_engine
python manage.py run_security_engine
python manage.py run_cve_engine
python manage.py run_lifecycle_engine
python manage.py run_discovery
python manage.py run_scheduler             # AUTHORITATIVE periodic scheduler (see below)
python manage.py run_check_engine          # service checks (HTTP/HTTPS/TCP)
python manage.py run_reachability_monitor  # TCP/22 device liveness + status
python manage.py collect_arp_mac --all     # ARP/MAC tables over SSH (scheduled every 6h)
python manage.py update_mac_vendors        # load IEEE OUI registry (scheduled weekly)
python manage.py reset_test_data           # dev: clear app data, keep auth users

## Scheduler Architecture
There is ONE scheduling system: the `run_scheduler` management-command loop (the
same management-command-loop pattern as run_config_manager / reachability-monitor
/ check-engine). Celery + django-celery-beat are present in requirements.txt but
are NOT used — no @shared_task or CELERY_BEAT_SCHEDULE is defined. Do NOT add a
second scheduling system; add periodic work to run_scheduler.

The compose `scheduler` service runs `python manage.py run_scheduler` and mounts
openbao-data:ro (so ARP/MAC collection can read each device's SSH credentials
from OpenBao). run_scheduler:
- Startup one-shots (idempotent): seed default/system alert rules (incl.
  temperature rules), unseal OpenBao / refresh the readable token, load the
  MAC-vendor OUI registry if the table is empty.
- Periodic (tick = --tick, default 300s): resolved-alert purge (daily), ARP/MAC
  collection (every 6h, ARP_MAC_COLLECT_INTERVAL_S), MAC-vendor OUI refresh
  (weekly, MAC_VENDOR_UPDATE_INTERVAL_S). The 6h/weekly tasks first fire one
  interval after startup so a restart doesn't stampede SSH or re-download the OUI
  CSV.

## Docker Compose Services
Infrastructure: postgres, influxdb, opensearch, valkey, nats, openbao
Application: api (port 8000), websocket (port 8001)
Frontend: frontend/nginx (port 3000)
Ingest: ingest-grpc, ingest-snmp, ingest-syslog, ingest-flow,
        ingest-otlp, ingest-api-poller
Engines: stream-processor, config-manager, alert-engine,
         cve-engine, lifecycle-engine, security-engine, scheduler,
         check-engine (service checks), reachability-monitor (TCP/22 liveness)

All engines build from ./services/api, but each gets its OWN image
(netpulse-<service>) — they do NOT share one image. `./netpulse.sh rebuild-api`
rebuilds every api-service image and recreates them with --no-deps (see
Development Workflow).

## Planned Features (NOT yet implemented)

The following are designed but not built — no models, endpoints, or services
exist for them yet. Do not document them as current.

- BGP looking glass: passive BGP route collector (e.g. ExaBGP), read-only.
  Planned models BGPSession/BGPPrefix, service bgp-monitor, endpoints /api/bgp/,
  /api/bgp/sessions/; frontend /bgp.
- Endpoint discovery: MAC address-table + ARP-table ingestion with OUI vendor
  lookup and IP/MAC search. Planned models MACEntry/ARPEntry, endpoint
  /api/endpoints/; frontend /endpoints.
- Alert routing Stage 2+: on-call schedules (OnCallSchedule/OnCallShift),
  acknowledgement/snooze (AlertAcknowledgement), Slack/PagerDuty/Webhook/SMS
  channels, and the visual escalation builder + on-call calendar UI. Stage 1
  (teams, policies, route matching, email) is built.
- SMS alerts (Twilio), NetPulse Collector agent, Helm chart, NetBox import,
  CVE applicability engine, lifecycle/EOL UI, bandwidth 95th-percentile trending.
- SSO beyond Stage 1: Azure AD + Okta (Stage 2), SAML 2.0 (Stage 3), LDAP/AD
  (Stage 4), login-page SSO buttons + Settings → Security → SSO UI (Stage 5).
  See "SSO / Single Sign-On" above. Stage 1 (Google OAuth2 backend) in progress.

## SNMP Trap Receiver
ingest-snmp handles both polling AND trap reception:
- UDP port 162 for incoming traps (v1, v2c, v3 informs)
- MIBs: RFC 1628 (UPS), APC, Eaton, standard network MIBs
- NATS topic: netpulse.telemetry.{device_id}.trap
- Critical: UPS on-battery, link state changes, hardware alerts

## Device Discovery
Four-tier system — all devices land in PENDING state:
1. Passive — detect from syslog/gNMI/flow/trap source IPs
2. Topology walk — CDP/LLDP + route table next-hop recursion
3. Active scanning — SNMP sweep, protocol probe sequence
4. Import — NetBox, DNA Center, CSV

Route table walking preferred over ping — ICMP often blocked.
OT/ICS WARNING: never auto-probe industrial control networks.
Safety: allowed_subnets, excluded_subnets, rate_limit_pps=10

### Discovery methods (DiscoveryJob.Method)
ping_snmp / topology / passive / scan / ping / import. The engine
(run_discovery) executes ping_snmp, ping, scan and topology; passive/import
have no engine run.
- ping_snmp (Ping + SNMP) — DEFAULT, production-safe: ICMP ping sweep (system
  `ping`, no nmap) → SNMP fingerprint (sysDescr/sysName/sysObjectID) + a
  non-intrusive SSH banner read of live hosts. No port scanning.
- ping (Ping Only) — ICMP sweep only; devices land platform-unknown for manual
  selection at approval.
- scan (Active Scan) — nmap host discovery + `-sV -O` service/OS detection.

⚠️ Active Scan (nmap) triggered a firewall block in the wco2 remote lab.
Always use Ping + SNMP as the default for production environments; reserve
Active Scan for lab/test networks. The New Job modal defaults to ping_snmp.

## API-Based Integrations
Service: ingest-api-poller
Vendors: Meraki, Mist/Aruba, UniFi, DNA Center, FortiCloud, Panorama
Plugin architecture: VendorAPIPlugin base class
Two modes: polling (60-300s) + webhooks (real-time push)
MSP: Meraki/Mist multi-org API support

## ChatOps (Phase 4)
Service: chatops-service
Platforms: Teams, Slack, Google Chat, Discord, Mattermost
Webhook endpoints: /api/webhooks/{platform}/
Queries: device status, site health, active alerts, CVEs, EOL
Security: map chat user → NetPulse RBAC, audit all queries

## Network Topology Mapping (Phase 4)
Auto-generated from CDP/LLDP with live utilization overlay
Cytoscape.js for topology, D3.js for NetFlow path visualization
Link colors: green <60%, yellow 60-80%, orange 80-90%, red 90%+
WAN circuit capacity overrides — physical speed ≠ committed rate
CircuitOverride model: committed_download_mbps, committed_upload_mbps,
  provider, circuit_id, monthly_cost, contract_renewal_date

## Availability & Uptime Reporting
Hierarchical: Organization → Region → Site → Device → Interface
Metrics: availability%, MTTR, MTBF, incident count
Maintenance windows excluded from SLA calculations
WAN circuit SLA tracking vs carrier commitment
Carrier dispute evidence reports

## Business Service Availability (Phase 5)
Map infrastructure → business services
ServiceComponent with required_count threshold
ServiceDependency for service-to-service relationships
Health: green/yellow/red based on component availability
Translates "2 servers down" → "E-Commerce at 60% capacity"

## TV/NOC Display Mode (Phase 4)
Route: /tv — full screen, no navigation
Auto-rotating views, large text, high contrast
Raspberry Pi kiosk deployment supported
Read-only token auth for always-on displays

## Distributed Remote Pollers
Poller nodes deployed close to monitored devices
Single outbound mTLS connection to central platform
Local disk buffer if central unreachable
Registration: one-time token → OpenBao PKI certificate
Poller assignment by subnet, manual override per device

## Data Persistence
Development: named Docker volumes
Production: bind mounts to ${DATA_DIR:-/opt/netpulse/data}/
Backup: docker compose stop → tar DATA_DIR → start

## RBAC Roles (seeded on startup)
Admin    — full platform access
Engineer — read/write devices, configs, alerts
Viewer   — read only
API      — service account for integrations

## SSO / Single Sign-On (Stage 1 in progress)
Enterprise login via external identity providers. Local admin login is ALWAYS
available as a fallback (set SSO_ALLOW_LOCAL_LOGIN=true); the first admin is a
local account from scripts/setup.sh.

### App + dependencies
- New app: apps/sso/ (model SSOProvider). Built on social-auth-app-django
  (social-auth core); python-jose for ID-token validation.
- requirements.txt: social-auth-app-django>=5.4.0, python-jose>=3.3.0
  (pin to a Django-6.0-compatible release — verify before adding).

### SSOProvider model
  name, provider, client_id, is_enabled, is_default, allow_signup,
  default_role (default 'viewer'), allowed_domains (ArrayField),
  tenant_id (Azure), okta_domain, saml_metadata_url
  client_secret is NOT a DB column — stored in OpenBao at
  secret/sso/{provider_id}/credentials (+ saml_private_key for SAML).

### Supported providers
  google-oauth2          Google Workspace        (Stage 1)
  azuread-tenant-oauth2  Microsoft Azure AD       (Stage 2)
  okta-oauth2            Okta                     (Stage 2)
  github                 GitHub OAuth2
  saml                   SAML 2.0                 (Stage 3, planned)
  ldap                   LDAP / Active Directory  (Stage 4, planned)

### Auth flow
1. User clicks an SSO button on the login page
2. Redirect to provider (Google/Azure/Okta)
3. Provider redirects back to /auth/complete/{backend}/
4. social-auth validates the token
5. Custom pipeline (apps/sso): enforce allowed_domains, assign default_role to
   new users, sync name/email from the IdP
6. Mint the SAME JWT as local auth (DRF SimpleJWT)
7. Redirect to the frontend with the token; frontend stores it, clears the URL

### Dynamic settings
social-auth reads provider keys from Django settings; NetPulse stores them in
the DB+OpenBao. A thin custom backend overrides get_setting() to return
client_id from SSOProvider and client_secret from OpenBao at request time.

### Security
- client_secret in OpenBao only — never PostgreSQL or logs
- allowed_domains enforced server-side (e.g. only @company.com)
- new SSO users get the viewer role by default; admin must elevate
- HTTPS required for OAuth2 callbacks (already enforced)
- validate redirect_uri (no open redirects)

### .env additions
  SSO_ALLOW_LOCAL_LOGIN=true
  SSO_DEFAULT_ROLE=viewer
  SOCIAL_AUTH_GOOGLE_OAUTH2_KEY= / _SECRET=
  SOCIAL_AUTH_AZUREAD_TENANT_OAUTH2_KEY= / _SECRET= / _TENANT_ID=
  SOCIAL_AUTH_OKTA_OAUTH2_KEY= / _SECRET= / _API_URL=
(Provider config is normally set in the UI → Settings → Security → SSO; these
env vars are an alternative for static config.)

### Frontend
- Login page: SSO buttons above the local username/password form; auto-redirect
  if a provider is is_default; local login always shown as fallback.
- Settings → Security → SSO Providers: list + add-provider wizard + Test.
- Profile: shows SSO connection status ("Signed in via Google").

### Build stages
  Stage 1: backend models + Google OAuth2   ← in progress
  Stage 2: Azure AD + Okta                  ← planned
  Stage 3: SAML 2.0                         ← planned
  Stage 4: LDAP                             ← planned
  Stage 5: frontend SSO buttons + settings UI

## Security Rules (NEVER violate)
1. Never store plaintext credentials anywhere
2. Always use OpenBao vault_path reference in PostgreSQL
3. Never return credential values in API responses
4. Always show "🔒 Stored securely in OpenBao" in credential UI
5. Scrub credentials from all logs
6. mTLS for all internal service communication
7. TLS 1.3 minimum for external connections
8. Zero secrets in code or environment variables in production

## Security Posture (implemented)
- ✅ H1 auth rate limiting — JWT token/refresh endpoints throttled (DRF
  ScopedRateThrottle, AUTH_THROTTLE_RATE, default 10/min) — blocks brute force.
  Keyed per client IP: NUM_PROXIES=1 + nginx X-Forwarded-For so it works behind
  the frontend proxy (not collapsed onto the shared nginx IP).
- ✅ HTTPS enforced — nginx redirects HTTP :3000 → HTTPS :3443
- ✅ OpenBao persistent secrets — credentials never in PostgreSQL/logs/API
- ✅ ALLOW_CONFIG_PUSH=false by default (read-only monitoring)
- ✅ SNMPv3 authPriv generated by default; SNMPv2c shows a plaintext warning
- ✅ ASCII sanitization of config before push/copy (sanitize_config_for_push)
- ✅ OT/ICS subnet exclusion enforced in discovery (excluded_subnets)
- ✅ Dependabot — weekly dependency updates (pip/npm/docker/actions)
- 📋 PagerDuty (planned)
- 📋 SMS alerts (planned)

## SNMP Defaults
- Default: SNMPv3 authPriv (auth + privacy) when the credential profile is v3
- Fallback: SNMPv2c — generated config carries a plaintext security warning
- Generated/previewed config shows write-only key placeholders
  (YOUR-AUTH-KEY-HERE); the actual auth/priv keys are fetched from OpenBao only
  at push time
- Per-platform CLI token mapping (IOS-XE "aes 128", NX-OS "aes-128",
  Junos "privacy-aes128", EOS "aes", FortiOS "aes")

## RBAC & Multi-Tenancy

### Phase 1 (Build Now) — Tenant-Level Isolation
Every object belongs to a tenant. Users scoped to tenant + role.

Tenant model:
  name, slug, plan, is_active, max_devices, max_users
  logo_url, primary_color (MSP white-labeling)

TenantUser model:
  user (FK), tenant (FK), role, is_active, invited_by, joined_at
  Roles: admin, engineer, viewer, api

All models inherit TenantModel (abstract):
  tenant = ForeignKey(Tenant)

All ViewSets inherit TenantViewSet:
  get_queryset() auto-filters by request.user.tenant

MSP Super Admin role:
  Sees all tenants
  Can switch tenant context
  Manages tenant provisioning
  Header shows "Viewing: {tenant} [Exit]" when in context

JWT token includes tenant_id and role:
  {user_id, username, tenant_id, tenant_slug, role}

### Phase 2 (Future) — Site/Group Scoping
  User role scoped to specific sites or device groups
  "Engineer at Site: Dallas, Austin only"

### Phase 3 (Future) — Object-Level (ABAC)
  Full attribute-based access control
  Granular per-feature permissions
  Custom role definitions

### Implementation Notes
- Add tenant to all existing models via migration
- Default tenant created on first run (single-org deployments)
- Single-org deployments work transparently (one tenant)
- Multi-tenant only relevant for MSP/cloud-hosted deployments
- Never expose cross-tenant data — enforce at QuerySet level
- Audit log includes tenant_id on every entry

## Configuration Backup & Git Sync

Two-tier config storage:

Tier 1 — Local (always on):
  PostgreSQL: metadata, hashes, version references
  Local disk: raw config files
  Path: ${DATA_DIR}/configs/{tenant}/{device}/{timestamp}.cfg

Tier 2 — Git Remote (optional):
  Providers: GitHub, GitLab (cloud/self-hosted), Gitea,
             Bitbucket, Generic Git (SSH or HTTPS)
  Auth: Personal Access Token, SSH Key, Deploy Key
  All git credentials stored in OpenBao

Git repo structure:
  devices/{hostname}/running-config.cfg (latest)
  devices/{hostname}/startup-config.cfg (latest)
  devices/{hostname}/metadata.json

Git commits:
  One commit per device per collection
  Meaningful commit messages with diff summary
  Drift detected → commit flagged with warning

Models:
  ConfigBackupSettings (per tenant):
    local_enabled, local_path, local_retention_days
    git_enabled, git_provider, git_repo_url, git_branch
    git_auth_method, git_vault_path (OpenBao ref)
    git_commit_author, git_commit_email, git_sync_frequency
    last_sync_at, last_sync_success, last_commit_sha

  DeviceConfig:
    device, tenant, config_type (running/startup/candidate)
    collected_at, collected_by, content, content_hash
    changed_from_previous, diff_summary
    git_commit_sha, local_path, compliance_status

Settings UI: Settings → Data Sources → Config Backup
  Local storage config + Git sync config
  Test Connection button for git remote
  Sync Now button
  Last sync status

Device Detail → Config tab:
  Version history list
  Side-by-side diff viewer
  View in GitHub/GitLab link
  Download config button
  Restore to previous version button

## Configuration Backup & Git Sync

Two-tier config storage:

Tier 1 — Local (always on):
  PostgreSQL: metadata, hashes, version references
  Local disk: raw config files
  Path: ${DATA_DIR}/configs/{tenant}/{device}/{timestamp}.cfg

Tier 2 — Git Remote (optional):
  Providers: GitHub, GitLab (cloud/self-hosted), Gitea,
             Bitbucket, Generic Git (SSH or HTTPS)
  Auth: Personal Access Token, SSH Key, Deploy Key
  All git credentials stored in OpenBao

Git repo structure:
  devices/{hostname}/running-config.cfg (latest)
  devices/{hostname}/startup-config.cfg (latest)
  devices/{hostname}/metadata.json

Git commits:
  One commit per device per collection
  Meaningful commit messages with diff summary
  Drift detected → commit flagged with warning

Models:
  ConfigBackupSettings (per tenant):
    local_enabled, local_path, local_retention_days
    git_enabled, git_provider, git_repo_url, git_branch
    git_auth_method, git_vault_path (OpenBao ref)
    git_commit_author, git_commit_email, git_sync_frequency
    last_sync_at, last_sync_success, last_commit_sha

  DeviceConfig:
    device, tenant, config_type (running/startup/candidate)
    collected_at, collected_by, content, content_hash
    changed_from_previous, diff_summary
    git_commit_sha, local_path, compliance_status

Settings UI: Settings → Data Sources → Config Backup
  Local storage config + Git sync config
  Test Connection button for git remote
  Sync Now button
  Last sync status

Device Detail → Config tab:
  Version history list
  Side-by-side diff viewer
  View in GitHub/GitLab link
  Download config button
  Restore to previous version button

## Sites/Locations

Site model (apps/devices/ or apps/core/):
  name, slug, description
  address, city, state, country
  latitude, longitude
  site_type: datacenter/campus/branch/remote/cloud
  tenant (FK), parent_site (FK self — hierarchy)
  contact_name, contact_email, contact_phone
  notes

API: /api/sites/ CRUD + /api/sites/{id}/devices/

Frontend route: /sites
  Sites list — table with hierarchy view option
  Site detail — tabs: Overview, Devices, Availability, WAN Circuits
  Create/edit modal with parent site dropdown

## NetBox Import

POST /api/import/netbox/
  netbox_url, api_token (OpenBao), import_options

Import order:
  1. Sites (map NetBox sites → NetPulse sites)
  2. Device roles
  3. Manufacturers + device types
  4. Devices with all metadata

NetBox v3 and v4 compatibility (auto-detect)
Skip existing devices (match by hostname/IP)
Store NetBox API token in OpenBao

Settings → Integrations → NetBox card
Import progress UI with live count
Import history with re-import option

## SNMP Polling Design

### Device-Level Polls (always on, no selection)
- System: sysDescr, sysName, sysUpTime, sysObjectID
- CPU: vendor-specific MIBs + hrProcessorLoad fallback
- Memory: ciscoMemoryPool or hrStorage
- Temperature: entPhysical sensors
- Power supplies: status per PSU
- Fans: status per fan
- BGP: peer state, uptime, prefix counts
- Hardware inventory: entPhysicalTable

### Interface-Level Polls (user selects)
Models:
  SNMPPollingConfig (OneToOne with Device):
    enabled, interval, poll_cpu, poll_memory,
    poll_temperature, poll_power, poll_fans,
    poll_bgp, poll_inventory

  MonitoredInterface:
    device (FK), if_index, if_name, if_description
    if_alias, if_speed_mbps, if_type
    lldp_neighbor, lldp_port, cdp_neighbor
    poll_traffic, poll_errors, poll_status
    circuit_override (FK, optional)
    last_discovered, last_status

### Interface Discovery UI
Device Detail → Telemetry → Interfaces tab
[Discover] button → SNMP walk ifTable + LLDP + CDP
Shows table: ☑ | Interface | Description | LLDP Neighbor
Regex filter box to find interfaces
Smart auto-select: UP + has description + has neighbor
Auto-exclude: loopbacks, tunnels, null, admin-down

### API Endpoints
POST /api/devices/{id}/interfaces/discover/
  SNMP walk → return list (does NOT save)
GET/POST /api/devices/{id}/interfaces/
DELETE /api/devices/{id}/interfaces/{if_index}/

### Key OIDs
ifHCInOctets, ifHCOutOctets (64-bit traffic)
ifOperStatus, ifAdminStatus
ifInErrors, ifOutErrors, ifInDiscards, ifOutDiscards
ifAlias, ifHighSpeed
lldpRemSysName, lldpRemPortDesc
cdpCacheDeviceId, cdpCacheDevicePort (Cisco)

### Smart Defaults on Discovery
Auto-select: operUp + has description OR has LLDP neighbor
Auto-exclude: loopback, tunnel, null, subinterfaces

## Telemetry Collection — Unified Design

### Key Decisions
- LLDP only — no CDP (open standard, vendor agnostic)
- Same interface selection UI for both SNMP and gNMI
- Auto-select collection method based on device capability
- gNMI preferred when available (faster, lower overhead)

### Models
TelemetryConfig (OneToOne with Device):
  primary_method: snmp/gnmi/both
  snmp_interval (default 300s)
  gnmi_interval (default 30s)
  collect_cpu, memory, temperature, power, fans,
  bgp, inventory, lldp (all BooleanField)

MonitoredInterface:
  device (FK), if_index (SNMP), if_name, if_description
  if_speed_mbps, if_type
  lldp_neighbor_hostname, lldp_neighbor_port, lldp_neighbor_desc
  poll_traffic, poll_errors, poll_status
  collection_method: auto/snmp/gnmi
  circuit_override (FK optional)
  last_discovered, last_status

### Interface Discovery
SNMP path: walk ifTable + ifXTable + lldpRemTable
gNMI path: Get /interfaces/interface/state
           Get /lldp/interfaces/interface/neighbors
Both produce same table format.

### gNMI Subscription Paths (OpenConfig)
Traffic: /interfaces/interface[name=X]/state/counters/in-octets
         /interfaces/interface[name=X]/state/counters/out-octets
Status:  /interfaces/interface[name=X]/state/oper-status
LLDP:    /lldp/interfaces/interface/neighbors/neighbor/state
CPU:     /components/component/cpu/utilization/state/instant
Memory:  /components/component/state/memory/utilized
BGP:     /network-instances/.../bgp/neighbors/neighbor/state

### Subscription Modes
ON_CHANGE: interface status (event-driven)
SAMPLE: traffic counters, CPU, memory (interval-based)
ONCE: discovery, inventory

### Smart Interface Auto-Select
Include: oper-status UP + (has description OR has LLDP neighbor)
Exclude: loopback, tunnel, null, subinterfaces, admin-down

### UI
Device Detail → Telemetry → Interfaces tab
Table: ☑ | Interface | Description | LLDP Neighbor | Method
Regex filter box
[Select All] [Select None] [Select Up Only]
[Discover] button → walks device → populates table
[Save] button → saves selected interfaces
Collection method badge per interface: [gNMI 10s] or [SNMP 5m]

## Interface State-Change Alerting

Per-interface up/down alerting driven by MonitoredInterface settings.

### MonitoredInterface fields (apps/telemetry)
  alert_on_down (default True) — alert when the interface goes down
  alert_on_up   (default True) — alert on recovery too
  alert_severity (critical/high/medium/low, default high)
  consecutive_polls_before_alert (default 1) — flap suppression
  last_status, last_status_changed — track state + downtime duration

### Alert engine (apps/alerts/interface_monitor.py)
  process_interface_status(iface, new_status, now) is called as interface
  status is observed (poller/stream-processor). On an up→down or down→up
  transition it persists the new state and raises an AlertEvent under a single
  system AlertRule ("Interface State Change"). Per-event severity, device,
  interface and title/message live in the event labels/annotations (the schema
  keeps severity on the rule). Recovery events are severity "info" and include
  the downtime duration. The first observation (from "unknown") only sets a
  baseline — no alert. Down/up alerts are suppressed when the matching toggle
  is off.

### API
  Interface alert fields are returned by GET /api/devices/{id}/interfaces/ and
  saved with the bulk interface selection. Bulk-apply alert settings:
  POST /api/devices/{id}/interfaces/alert-config/
    { if_names: [...], alert_on_down, alert_on_up, alert_severity,
      consecutive_polls_before_alert }

### UI
  Telemetry Configuration slide-over → interface table: per-row alert toggle
  + expandable alert settings; toolbar bulk Enable/Disable Alerts for selected.
  Alerts page / device Recent Alerts: interface alerts show the interface name,
  red for down / green for recovery, link to the device.

## SNMP Polling Timer Settings

Two levels:

Global (Settings → Polling):
  SNMPGlobalSettings model (singleton per tenant):
  device_metrics_interval: 300s default
  interface_traffic_interval: 300s default
  interface_status_interval: 60s default
  bgp_interval: 60s default
  inventory_interval: 3600s default
  lldp_interval: 3600s default
  
  Also: max_concurrent_sessions, timeout, retries,
        bulk_get_enabled, bulk_get_max_repetitions

Per-device override (Device Detail → Telemetry):
  TelemetryConfig additions:
  override_intervals: BooleanField(default=False)
  device_metrics_interval: IntegerField(null=True)
  interface_traffic_interval: IntegerField(null=True)
  interface_status_interval: IntegerField(null=True)
  bgp_interval: IntegerField(null=True)
  
  Presets: Normal / Troubleshooting (30s) / Reduced (600s) / Custom

Effective interval = device override if set, else global default

gNMI/gRPC intervals: controlled on device via generated config
  NOT configurable in NetPulse — document with UI tooltip
  To change: regenerate config → push to device

Settings sub-nav: add "Polling" between General and Credentials

## SSL/TLS Certificate Management (NetPulse UI)
Settings → System → SSL/TLS section
Secures NetPulse web UI with HTTPS — NOT for network devices.

Workflow:
1. Generate CSR + private key (key stays on server)
2. Admin submits CSR to CA (Let's Encrypt, DigiCert, internal PKI)
3. Admin uploads signed cert back to NetPulse
4. NetPulse validates cert+key pair, writes nginx SSL config
5. Nginx reloads → HTTPS enabled, HTTP redirects to HTTPS

Three install methods:
- Generate CSR → get cert from CA (recommended)
- Self-signed → testing/internal (browser warning)
- Upload existing → bring your own cert+key

Files stored in Docker volume ssl-certs:
  /etc/ssl/netpulse/netpulse.key (mode 600)
  /etc/ssl/netpulse/netpulse.crt
  /etc/ssl/netpulse/netpulse-chain.crt (optional)
  /etc/ssl/netpulse/netpulse.csr

Private key NEVER returned to browser — server-side only.

Nginx SSL config:
  TLS 1.2 + 1.3 only
  Strong cipher suites
  HTTP → HTTPS redirect
  HSTS header
  X-Forwarded-Proto: https to Django

Docker Compose:
  ssl-certs volume shared between api and frontend
  Port 443 exposed on frontend service

Monitoring:
  /api/health/ returns ssl_cert_days_remaining
  Dashboard warning when < 30 days remaining
  
API endpoints:
  POST /api/settings/system/ssl/generate-csr/
  POST /api/settings/system/ssl/upload-certificate/
  POST /api/settings/system/ssl/self-signed/
  POST /api/settings/system/ssl/enable-https/
  GET  /api/settings/system/ssl/status/

## CA Certificate Management
Settings → System → SSL/TLS → Trusted CA Certificates

Model: CACertificate
  name, subject, issuer, fingerprint (unique)
  not_before, not_after, cert_pem
  file_path, is_root, is_intermediate
  added_by (FK User), created_at

Storage:
  Individual: /etc/ssl/netpulse/ca-certs/{uuid}.crt
  Bundle: /etc/ssl/netpulse/ca-bundle.crt (auto-rebuilt)
  
Used for:
  - nginx ssl_trusted_certificate (OCSP stapling)
  - All outbound HTTPS: CVE feeds, vendor APIs, git sync
  - Python requests via REQUESTS_CA_BUNDLE env var
  - Critical for orgs with SSL inspection/internal PKI

API:
  GET/POST /api/settings/system/ssl/ca-certs/
  DELETE   /api/settings/system/ssl/ca-certs/{id}/
  POST     /api/settings/system/ssl/ca-certs/{id}/verify/

Supported upload formats: PEM, DER (auto-convert), PKCS#7
Rebuild ca-bundle.crt + reload nginx on every add/delete
Warn before deleting CA that issued installed server cert

Expiry monitoring:
  Red: expired
  Orange: < 90 days remaining
  Dashboard warning included



## Log Viewing

### Device Logs Tab
Device Detail → Logs tab (between Telemetry and Configuration)
Filters: severity, time range, message text search
Severity color coding: red=critical+, orange=error, 
  yellow=warning, blue=info, gray=debug
Auto-refresh toggle (30s)
Pagination: 50 per page

### Fleet Logs Page (/logs)
Top-level sidebar nav between Alerts and CVE
Filters: device, site, role, severity, time, text search
Summary bar: total + count by severity
Click row → expand details
Click device → navigate to device logs tab

### Backend
GET /api/logs/
Query params: device_hostname, device_id, site, severity,
  from, to, search, page, page_size
Queries OpenSearch index: netpulse-logs-*
Returns: {count, results[], summary:{by_severity}}

OpenSearch must be running and stream-processor must be
consuming netpulse.logs.> from NATS to populate logs.

## Topology Map

### TopologyLink Model
device_a (FK), port_a
device_b (FK), port_b
discovered_via: lldp (only - no CDP)
link_speed_mbps, last_seen
unique_together: device_a + port_a

### LLDP Discovery
POST /api/devices/{id}/topology/discover/
SNMP walk lldpRemTable → match neighbors to Device records
Auto-run after config collection if SNMP credential available

### Topology API filters
GET /api/devices/topology/?site=X&device=Y&depth=N&role=Z
depth: 1/2/3/all hops from center device

### Frontend filters
Site dropdown, Device dropdown (center), 
Depth (1/2/3/All hops), Role dropdown
[Discover Links] button → triggers LLDP walk all devices

### Node display
Smaller nodes with device type icon
Color by status, hostname label below

### Edge display
Lines between LLDP-connected devices
Color by utilization, thickness by speed
Hover: show port names each side
Click: link detail popup

## CVE/Advisory Feed Sources

### Direct APIs (require credentials)
Cisco PSIRT openVuln API:
  URL: https://apix.cisco.com/security/advisories/v2
  Auth: OAuth2 (CLIENT_ID + CLIENT_SECRET → bearer token)
  Covers: All Cisco products
  Rate: 5/sec, 30/min, 5000/day

Palo Alto PSIRT API (beta):
  URL: https://security.paloaltonetworks.com/api
  Auth: Contact psirt@paloaltonetworks.com
  Status: Beta

### Free Public Feeds (no key needed)
NVD API v2:
  URL: https://services.nvd.nist.gov/rest/json/cves/2.0
  Auth: Optional API key (higher rate limits with key)
  Rate: 5/30s without key, 50/30s with key

CISA KEV (Known Exploited Vulnerabilities):
  URL: https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json
  Auth: None
  Update: Daily — flag these as highest priority

HPE/Aruba CSAF feed:
  URL: https://csaf.arubanetworking.hpe.com
  Auth: None
  Format: CSAF standard

Fortinet CSAF feed:
  Auth: None — public CSAF

### Community YAML (open source contribution model)
Juniper JSAs: no API — use community YAML in repo
Arista advisories: no API — use community YAML in repo
MikroTik, VyOS, etc: community YAML

### Third-Party Aggregators (future)
VulnCheck: commercial, aggregates 100+ vendor feeds
GitHub Advisory DB: free, software packages primarily

## Interface State Change Alerting

MonitoredInterface alert fields:
  alert_on_down: BooleanField(default=True)
  alert_on_up: BooleanField(default=True)  ← recovery notification
  alert_severity: critical/high/medium/low (default=high)
  consecutive_polls_before_alert: IntegerField(default=1)
  last_status_changed: DateTimeField(null=True)

Alert engine behavior:
  On status change up→down:
    Create AlertEvent: severity=interface.alert_severity
    Title: "Interface Down: {device} {interface}"
    Message includes: previous status, timestamp
  On status change down→up (recovery):
    Create AlertEvent: severity=info
    Title: "Interface Recovered: {device} {interface}"
    Message includes: downtime duration

Flapping protection:
  consecutive_polls_before_alert=N means N consecutive
  down polls required before alerting
  Prevents false alerts from brief link flaps

UI: Bell icon toggle per interface in Telemetry Config
Bulk enable/disable for selected interfaces

## PINNED — First-Run Setup Script ✅ IMPLEMENTED

### scripts/setup.sh  (built — see the script for current behavior)
Interactive bash script for first-time deployment.
Run once after git clone, before docker compose up.

Prompts for:
1. Basic config:
   - Platform hostname/domain (for SSL cert SANs)
   - Collector IP address (COLLECTOR_IP)
   - Timezone

2. Credentials (with validation):
   - Django admin username (default: admin)
   - Django admin password (min 12 chars, complexity check)
   - PostgreSQL password
   - OpenBao root token / unseal key (auto-generate option)
   - NATS username + password
   - InfluxDB admin password
   - OpenSearch admin password
   - Valkey password (optional)

3. Optional integrations:
   - NVD API key (show signup URL)
   - Cisco PSIRT client ID + secret (show signup URL)
   - SMTP settings for email alerts
   - Collector IP confirmation

4. Script behavior:
   - Reads .env.example as template
   - Writes values to .env (never commits)
   - Generates secure random passwords if user skips
     (using openssl rand -base64 32)
   - Shows summary of what was configured
   - Validates each input before accepting
   - Idempotent — can be re-run to update specific values
   - Colorized output (green=ok, yellow=warning, red=error)

5. After .env is written:
   - Optionally run: docker compose pull
   - Optionally run: docker compose up -d
   - Show access URLs:
     "NetPulse is available at http://{COLLECTOR_IP}:3000"
     "API docs: http://{COLLECTOR_IP}:8000/api/docs/"

6. Security checks:
   - Warn if default passwords are kept
   - Warn if running as root
   - Warn if ports 80/443 already in use
   - Check Docker and Docker Compose are installed
   - Check minimum RAM (4GB recommended)
   - Check disk space (20GB recommended)

Example flow:
  $ ./scripts/setup.sh
  
  ╔═══════════════════════════════╗
  ║   NetPulse First-Run Setup   ║
  ╚═══════════════════════════════╝
  
  This script configures NetPulse for first deployment.
  Press Enter to accept defaults shown in [brackets].
  
  → Platform hostname [netpulse.local]: netpulse.company.com
  → Collector IP [auto-detect: 192.168.98.134]: 
  → Admin username [admin]: 
  → Admin password (min 12 chars): ************
  → Confirm password: ************
  ✅ Password strength: good
  
  → Configure NVD API key? (y/N): y
  → NVD API key: ****
  → Signup at: https://nvd.nist.gov/developers/request-an-api-key
  
  ┌─────────────────────────────────┐
  │ Configuration Summary           │
  ├─────────────────────────────────┤
  │ Hostname:    netpulse.company   │
  │ Collector:   192.168.98.134     │
  │ Admin user:  admin              │
  │ NVD API:     configured ✅      │
  │ Cisco PSIRT: not configured     │
  └─────────────────────────────────┘
  
  Write configuration to .env? (Y/n): 
  ✅ .env written successfully
  
  Start NetPulse now? (Y/n):
  🚀 Starting NetPulse...
  ✅ NetPulse is running!
  
  Access at: http://192.168.98.134:3000
  API docs:  http://192.168.98.134:8000/api/docs/

✅ IMPLEMENTED — scripts/setup.sh exists.

## PINNED — Config Push Safety Flag ✅ IMPLEMENTED

### Environment variable: ALLOW_CONFIG_PUSH
Controls whether NetPulse can push configuration changes
to network devices. Default: false (read-only mode).

.env / .env.example:
  # Allow NetPulse to push configuration to devices
  # Set to true only after review by network team
  # Default: false (read-only, monitoring only)
  ALLOW_CONFIG_PUSH=false

Backend behavior:
  All endpoints that push config to devices must check:
  from django.conf import settings
  if not settings.ALLOW_CONFIG_PUSH:
      return Response(
          {"error": "Config push is disabled. Set ALLOW_CONFIG_PUSH=true to enable."},
          status=403
      )
  
  Affected endpoints:
  - POST /api/devices/{id}/telemetry-config/push/
  - POST /api/devices/{id}/remediation/push/
  - Any future config push endpoints

Frontend behavior:
  When ALLOW_CONFIG_PUSH=false:
  - "Push to Device" button is disabled (grayed out)
  - Tooltip: "Config push is disabled by administrator.
              Contact your network team to enable."
  - Copy to Clipboard still works
  - Generated config still visible (read-only)
  
  When ALLOW_CONFIG_PUSH=true:
  - "Push to Device" button active
  - Shows confirmation modal before pushing
  - Audit log entry on every push

  Frontend reads flag from:
  GET /api/settings/system/ 
  Add: allow_config_push: bool to response
  So frontend knows without hardcoding.

Django settings:
  ALLOW_CONFIG_PUSH = env.bool('ALLOW_CONFIG_PUSH', default=False)

Audit trail regardless of setting:
  Log every push ATTEMPT (successful or blocked)
  So admins can see what would have been pushed
  even in read-only mode.

✅ IMPLEMENTED — ALLOW_CONFIG_PUSH is read in settings and enforced in the
config-push/remediation endpoints; the frontend reads it from /api/settings/system/.

## PINNED — gNMI/SNMP Adaptive Polling ✅ IMPLEMENTED

When a device is actively pushing gNMI telemetry, SNMP polling
for the same metrics is redundant and wastes device resources.

### As built (differs slightly from the original sketch below)
- ingest-grpc (mdt_servicer) stamps `gnmi:last_seen:{device_id}` in Valkey on
  every gNMI/MDT message — keyed by the registry-resolved NUMERIC device_id
  only (hostname/IP fallbacks are skipped so the key matches what ingest-snmp
  looks up). TTL 180s (3× the 30s interval). GNMIHeartbeat is best-effort —
  Valkey errors never interrupt ingest.
- ingest-snmp (poller) skips the ENTIRE device poll while gNMI is active
  (heartbeat < GNMI_ACTIVE_THRESHOLD=120s); the simplest-approach "SNMP is
  purely a fallback". Logs only on transition: "Skipping SNMP device metrics
  for device {id} - gNMI active" / "gNMI stream timeout for device {id} -
  resuming SNMP fallback polling". GNMIActivity degrades to "poll normally"
  (logged) if Valkey is unavailable. Disable via ADAPTIVE_POLLING=false.
- API /devices/{id}/collection-status/ returns snmp.suppressed +
  suppressed_reason="gNMI active" (active=false) while gNMI is streaming;
  last_poll_seconds_ago is still reported.
- UI: header shows 📡 gNMI only (tooltip notes "SNMP polling suppressed"); the
  Telemetry-tab bar shows a "SNMP polling suppressed" line and a transient
  "gNMI stream lost — falling back to SNMP polling" notice on failover.
- Valkey URL is built from VALKEY_HOST/PORT/PASSWORD (password URL-encoded) or
  an explicit VALKEY_URL.

### Original design sketch (superseded by the above)

### Logic:
1. Track last gNMI message received per device in Valkey:
   Key: "gnmi:last_seen:{device_id}"
   Value: timestamp
   TTL: 120 seconds (2x the expected gNMI interval)

2. In publish_device_configs / SNMP poller:
   Before publishing SNMP poll config, check Valkey:
   If gnmi:last_seen:{device_id} exists and is recent:
     → Skip SNMP polling for device metrics (CPU/memory/uptime)
     → Keep SNMP polling for anything not covered by gNMI
     → Log: "device {hostname}: gNMI active, skipping SNMP metrics"
   
   If gNMI stream goes stale (TTL expires):
     → Re-enable SNMP polling automatically
     → Log: "device {hostname}: gNMI stream lost, falling back to SNMP"

3. ingest-grpc on receiving gNMI message:
   → Update Valkey key: SET gnmi:last_seen:{device_id} {timestamp} EX 120
   → Publish to NATS as normal

4. UI indicator on Telemetry tab:
   Show collection method badge:
   "📡 gNMI streaming" (green) when gNMI active
   "📊 SNMP polling" (blue) when SNMP only
   "⚠️ No telemetry" (yellow) when neither

5. TelemetryConfig.primary_method field already exists:
   Values: snmp, gnmi, otlp
   Update automatically based on what's actually received.

### Priority: implement after gNMI port fix is verified working
### ✅ Built — verified live: router1/router2 stream gNMI → SNMP suppressed.

## PINNED — Pre-Production Security Audit

Before any production deployment, run a full security
audit of the codebase. Do NOT skip this step.

### Automated scanning:
1. Python dependency vulnerabilities:
   pip-audit --requirement services/api/requirements.txt
   safety check -r services/api/requirements.txt

2. Static analysis (SAST):
   bandit -r services/api/ -f json -o bandit-report.json
   semgrep --config=auto services/api/
   semgrep --config=p/django services/api/

3. Frontend dependencies:
   cd services/frontend && npm audit --audit-level=moderate

4. Docker image scanning:
   docker scout cves netpulse-api
   trivy image netpulse-api
   trivy image netpulse-frontend

5. Secrets scanning (verify none leaked):
   truffleHog git file://. --only-verified
   gitleaks detect --source . -v

### Manual code review checklist:
Claude should review each category:

Authentication & Authorization:
□ All API endpoints require authentication
□ Role-based permissions enforced (not just is_authenticated)
□ JWT tokens have appropriate expiry
□ No authentication bypass possible
□ Admin endpoints restricted to admin role
□ Service-to-service auth uses tokens not passwords

Input Validation:
□ All user input validated and sanitized
□ No SQL injection possible (ORM used correctly)
□ No command injection (subprocess calls safe)
□ No path traversal in file operations
□ IP address inputs validated
□ CIDR/prefix inputs validated

Secrets Management:
□ No hardcoded secrets anywhere in code
□ No secrets in logs
□ No secrets in error messages returned to client
□ OpenBao used for all credentials
□ .env not committed to git
□ No secrets in Docker environment that get logged

API Security:
□ Rate limiting on auth endpoints
□ Rate limiting on expensive operations
□ No verbose error messages in production
□ CORS configured correctly
□ No CSRF vulnerabilities
□ Request size limits configured

Network Security:
□ All internal service communication uses auth
□ OpenBao requires token for all reads
□ NATS requires credentials
□ InfluxDB requires token
□ OpenSearch requires credentials
□ No services exposed unnecessarily

Data Security:
□ Sensitive data encrypted at rest (OpenBao)
□ Passwords hashed (never plaintext)
□ PII handled appropriately
□ Audit log for all credential access
□ No sensitive data in URL parameters

SSH/Device Access:
□ SSH credentials never logged
□ SSH connections use key or password from OpenBao only
□ No credential caching in plaintext
□ SSH host key verification (or documented exception)

Docker Security:
□ Containers run as non-root user
□ No privileged containers
□ Minimal base images
□ No unnecessary capabilities
□ Read-only filesystems where possible
□ No secrets in Dockerfile or docker-compose.yml

Dependencies:
□ All dependencies pinned to specific versions
□ No known CVEs in dependencies
□ License compliance (all Apache 2.0/MIT/BSD)
□ No abandoned/unmaintained packages

### Output required:
After audit, produce:
1. SECURITY-REPORT.md with findings
2. CRITICAL issues (must fix before production)
3. HIGH issues (should fix before production)
4. MEDIUM issues (fix in first patch)
5. LOW/INFO (track for future)
6. Remediation steps for each finding

### Do NOT deploy to production until:
□ All CRITICAL findings resolved
□ All HIGH findings resolved or accepted with justification
□ Automated scans show no new critical CVEs
□ Manual checklist completed and signed off

Do NOT run this audit until explicitly requested.
This is a pre-production gate, not a development task.

## Pre-release / Production Checklist
Before public v1.0 announcement:
- [ ] Remove or restrict show_credentials management command
      (apps/credentials/management/commands/show_credentials.py —
      shows credential info; gated only by being a server-side command)
- [ ] Remove scripts/check_keys.py if still present
- [ ] Audit all management commands for security-sensitive output
- [ ] Review DEBUG settings in production
- [ ] Ensure SECRET_KEY rotation documented
- [ ] Review ALLOWED_HOSTS configuration
- [ ] SSL/TLS certificate setup docs
- [ ] Remove any hardcoded test credentials from documentation/examples

Collector deployment (post v1.0):
- [ ] Create docker-compose.collector.yml
- [ ] Add role selection to setup.sh (Full Stack vs Collector)
- [ ] Build collector-agent forwarding service (mTLS, local buffer, replay)
- [ ] collector registers with central via Collector model (already built)
- [ ] Test multi-collector deployment
- [ ] Document collector setup

## PINNED — Monorepo + multiple compose files
- One repo (travisjohnsonga/netpulse)
- docker-compose.yml = full stack (default)
- docker-compose.collector.yml = future collector
- setup.sh will ask deployment role
- Shared ingest service images, no sync issues
- Decision made: 2026-06-03

## PINNED — ChatOps User Identity & Profile Integration

### Problem:
When escalating alerts to specific users, we need to know
their identity on each chat platform (Slack, Discord, etc).
Email is easy (user.email) but chat platforms need handles.

### User Profile ChatOps Fields:
Add to UserPreferences or User model:

slack_user_id: CharField(null=True, blank=True)
  ← Slack member ID (e.g. U01234ABCDE)
  ← Found in Slack: click profile → ... → Copy member ID
  
discord_user_id: CharField(null=True, blank=True)
  ← Discord user ID (18-digit number)
  ← Found in Discord: enable Developer Mode → right-click → Copy ID
  
pagerduty_email: CharField(null=True, blank=True)
  ← Email address used in PagerDuty account

teams_user_id: CharField(null=True, blank=True)
  ← Microsoft Teams user ID (for future)

### Profile Page - Conditional Display:
Only show chat platform fields if that platform
is configured/enabled at the system level.

Check: does any Team have slack_webhook_url set?
  → Show Slack user ID field in profile
  
Check: does any Team have discord_webhook_url set?
  → Show Discord user ID field in profile

Check: PAGERDUTY_DEFAULT_KEY in settings?
  → Show PagerDuty email field in profile

Profile UI section:
  ── Chat & Alerting ────────────────────────────
  
  [Only shown if Slack is configured]
  Slack Member ID: [U01234ABCDE        ]
  How to find: Slack → Your profile → ⋮ → Copy member ID
  
  [Only shown if Discord is configured]  
  Discord User ID: [123456789012345678 ]
  How to find: Discord → Settings → Advanced → 
               Enable Developer Mode → Right-click 
               your name → Copy User ID
  
  [Only shown if PagerDuty configured]
  PagerDuty Email: [john@company.com   ]
  
  ── ────────────────────────────────────────────

### Alert Escalation with ChatOps:
When executing escalation step for specific user:

For Slack DM:
  POST to Slack API chat.postMessage
  channel: user.slack_user_id (DM to user)
  vs
  channel: team.slack_channel (team channel)

For Discord DM:
  Requires bot token (not just webhook)
  OR: mention user in channel webhook:
  f"<@{user.discord_user_id}> Alert: {message}"
  
  Simplest approach: include @mention in webhook message
  so user gets notified in the channel:
  "🔴 <@123456789012345678> Device Down: router1"

### Implementation Priority:
1. Add fields to UserPreferences model
2. Show conditionally in /profile based on 
   enabled chat platforms
3. Use in escalation engine when notifying
   specific users (mention in channel)
4. Later: true DMs require bot tokens

### Do NOT build until requested.
## PINNED — Support Bundle Generator

### Purpose:
Generate a diagnostic bundle that gives Claude (or any
engineer) enough context to diagnose and fix issues
without needing live access to the system.
Attach to GitHub issues for async troubleshooting.

### UI Location:
Settings → Support (new section)
Or: Help → Generate Support Bundle

### Page Layout:
┌─────────────────────────────────────────────────────┐
│ Support Bundle                                       │
│ Generate a diagnostic package for troubleshooting   │
├─────────────────────────────────────────────────────┤
│ Describe your issue:                                │
│ ┌───────────────────────────────────────────────┐  │
│ │ Device 2 isn't showing LLDP neighbors in the  │  │
│ │ topology map despite being connected to       │  │
│ │ router1 via GigabitEthernet1...               │  │
│ └───────────────────────────────────────────────┘  │
│ Placeholder: "e.g. Device 2 isn't showing LLDP     │
│ neighbors, or alerts aren't escalating for item X" │
│                                                     │
│ Bundle Type:                                        │
│ ○ Quick Bundle (last 1h - logs, recent errors)     │
│   ~500KB - fast to generate                        │
│ ● Full Bundle (last 24h - all diagnostic data)     │
│   ~5MB - comprehensive                             │
│                                                     │
│ [Generate Quick Bundle] [Generate Full Bundle]      │
└─────────────────────────────────────────────────────┘

### Bundle Contents:

#### Quick Bundle (last 1h):
support_bundle_{timestamp}/
├── ISSUE_DESCRIPTION.txt     ← user's description
├── SYSTEM_INFO.json          ← platform version, config
├── SERVICE_STATUS.json       ← all container health
├── logs/
│   ├── api_errors.log        ← ERROR level only, last 1h
│   ├── stream_processor.log  ← last 1h
│   ├── ingest_snmp.log       ← last 1h
│   ├── ingest_grpc.log       ← last 1h
│   └── check_engine.log      ← last 1h
├── database/
│   ├── devices.json          ← all devices + status
│   ├── topology_links.json   ← all topology links
│   ├── monitored_interfaces.json
│   ├── service_checks.json   ← checks + recent results
│   ├── alert_events.json     ← last 50 alerts
│   └── alert_rules.json      ← configured rules
├── telemetry/
│   ├── influxdb_summary.json ← measurements + counts
│   └── recent_metrics.json   ← last 10min per device
├── config/
│   ├── settings_summary.json ← non-secret settings
│   └── enabled_features.json
└── README.txt                ← how to use bundle

#### Full Bundle (last 24h) - adds:
├── logs/
│   └── {all services}_24h.log
├── database/
│   ├── alert_history.json    ← all alerts 24h
│   ├── check_results.json    ← all check results 24h
│   ├── config_backups.json   ← recent configs
│   └── topology_history.json
├── telemetry/
│   └── influxdb_export.json  ← full 24h metrics
├── opensearch/
│   ├── log_sample.json       ← 100 recent logs
│   └── index_stats.json
└── network/
    ├── nats_stats.json
    ├── openbao_health.json   ← NO secrets, just health
    └── service_connectivity.json

### SECURITY - Never include in bundle:
❌ Passwords or API keys
❌ SSH private keys
❌ OpenBao secrets
❌ JWT tokens
❌ SNMP community strings or auth keys
❌ User passwords
❌ .env file contents
❌ TLS private keys

Scrub before including:
- Replace secrets with "[REDACTED]"
- Mask IP addresses option (for privacy)
- Remove sensitive log lines containing passwords

### SYSTEM_INFO.json contents:
{
  "netpulse_version": "git describe --tags",
  "generated_at": "2026-05-31T...",
  "issue_description": "user text here",
  "bundle_type": "full|quick",
  "platform": {
    "os": "Ubuntu 24.04",
    "docker_version": "29.5.2",
    "python_version": "3.13",
    "django_version": "6.0"
  },
  "services": {
    "total": 24,
    "healthy": 24,
    "unhealthy": 0
  },
  "devices": {
    "total": 2,
    "active": 2,
    "unreachable": 0
  },
  "telemetry": {
    "snmp_polling": true,
    "gnmi_streaming": true,
    "devices_streaming": 2
  },
  "features": {
    "allow_config_push": false,
    "https_enabled": true,
    "openbao_sealed": false
  }
}

### API Endpoint:
POST /api/support/bundle/
Body: {
  "description": "user text",
  "bundle_type": "quick|full"
}

Response: 
{
  "bundle_id": "uuid",
  "status": "generating",
  "estimated_seconds": 30
}

GET /api/support/bundle/{id}/status/
→ {status: "ready", download_url: "/api/support/bundle/{id}/download/"}

GET /api/support/bundle/{id}/download/
→ Returns ZIP file: netpulse_support_{timestamp}.zip

### Generation:
Backend generates bundle asynchronously:
- Collect all data
- Scrub secrets
- Write to temp directory
- ZIP compress
- Store in /tmp/netpulse_bundles/ (TTL 1h)
- Return download URL

### GitHub Issue Template:
Include in bundle README.txt:
"To report this issue:
1. Go to https://github.com/travisjohnsonga/netpulse/issues/new
2. Click 'Bug Report'
3. Attach netpulse_support_{timestamp}.zip
4. Copy your issue description from ISSUE_DESCRIPTION.txt
5. Include NetPulse version from SYSTEM_INFO.json"

Also add .github/ISSUE_TEMPLATE/bug_report.md:
  Fields: 
  - NetPulse version
  - Issue description  
  - Steps to reproduce
  - Expected vs actual behavior
  - [ ] Support bundle attached

### Frontend progress indicator:
While generating show:
  Collecting service status... ✅
  Collecting device data... ✅
  Collecting logs... ⏳
  Scrubbing sensitive data... 
  Compressing bundle...
  
  [████████░░] 80% - Collecting logs...

### Do NOT build until requested.


## PINNED — Firewall Traffic Log Analytics

### Vision:
Forward firewall traffic logs to NetPulse instead of
logging into FortiManager, Panorama, or each firewall
individually. OpenSearch handles the volume easily.

### Supported Sources:
- Fortinet FortiOS (syslog key=value format) ✅ already ingesting
- Palo Alto PAN-OS (syslog CEF or custom format)
- Cisco ASA/FTD (syslog)
- pfSense/OPNsense (syslog)
- iptables/nftables (via rsyslog)
- AWS VPC Flow Logs (via OTLP/S3)
- Azure NSG Flow Logs (planned)

### Data Model:
New OpenSearch index: netpulse-firewall-{YYYY.MM}

FirewallTrafficLog:
  timestamp: datetime
  device_id: FK → Device
  device_name: str
  
  # Traffic
  action: allow/deny/drop/reset/client-rst/server-rst
  direction: inbound/outbound/forward/local
  
  # Source
  src_ip: IP
  src_port: int
  src_country: str
  src_zone: str (FortiOS vdom/zone)
  src_interface: str
  
  # Destination  
  dst_ip: IP
  dst_port: int
  dst_country: str
  dst_zone: str
  dst_interface: str
  
  # Application
  application: str (HTTPS, SSH, DNS, etc)
  service: str
  protocol: TCP/UDP/ICMP
  
  # Policy
  policy_id: int
  policy_name: str
  
  # Stats
  duration_seconds: int
  bytes_sent: int
  bytes_received: int
  packets_sent: int
  packets_received: int
  
  # Security
  threat_name: str (null if clean)
  threat_level: str
  action_taken: str (blocked/allowed/monitored)
  
  # Raw
  raw_message: str

### UI: /firewall page

Traffic Log Viewer:
  Similar to Logs page but firewall-specific filters:
  
  Filters:
  [Source IP/CIDR] [Dest IP/CIDR] [Port] 
  [Action: Allow/Deny] [Application] [Policy]
  [Device] [Time range]
  
  Table:
  Time | Device | Action | Src IP | Dst IP | Port | App | Bytes | Duration

Traffic Analytics:
  Top denied destinations (blocked threats)
  Top talkers by bytes
  Geographic map of traffic sources
  Application breakdown pie chart
  Hourly traffic volume chart
  Policy hit counts

Security Dashboard:
  Blocked connections by country
  Top blocked applications
  Policy violations
  Geographic threat map

### Alerting on traffic patterns:
  High deny rate from single IP → possible scan/attack
  New country seen in traffic → anomaly
  Large data transfer → possible exfiltration
  Port scan detection (many ports, same src)

### Performance:
  OpenSearch handles millions of logs/day easily
  Retention: configurable (default 30 days for traffic)
  Aggregations for analytics (pre-computed)
  Index lifecycle management for cost control

### Syslog already working for FortiOS:
  FortiOS traffic logs already arriving via syslog
  Just need to:
  1. Parse traffic-specific fields
  2. Store in separate firewall index
  3. Build traffic analytics UI

### Do NOT build until requested.
### Priority: Medium (after core features complete)

## PINNED — gNMI Capability Discovery & Selective Subscriptions

### Vision:
Instead of hardcoding subscription paths per platform,
query the device's gNMI capabilities endpoint to discover
what YANG models and paths are actually supported.
Engineers then selectively choose what to subscribe to.

### gNMI Capabilities RPC:
gNMI spec defines a Capabilities() RPC:
  Request:  CapabilityRequest {}
  Response: CapabilityResponse {
    supported_models: [ModelData]
    supported_encodings: [Encoding]
    gNMI_version: string
  }
  
  ModelData {
    name: string      (e.g. "Cisco-IOS-XE-interfaces-oper")
    organization: str (e.g. "Cisco Systems, Inc.")
    version: string   (e.g. "2022-11-01")
  }

### Implementation:

1. New API endpoint:
   POST /api/devices/{id}/gnmi/capabilities/
   
   Connects to device gNMI port (57400 or 6030 for Arista)
   Sends CapabilityRequest
   Returns list of supported YANG models + encodings
   
   Response:
   {
     "gnmi_version": "0.7.0",
     "supported_encodings": ["JSON_IETF", "PROTO"],
     "models": [
       {
         "name": "Cisco-IOS-XE-interfaces-oper",
         "organization": "Cisco Systems, Inc.",
         "version": "2022-11-01",
         "category": "interfaces",  ← derived
         "paths": [                  ← known paths for this model
           "/interfaces-ios-xe-oper:interfaces",
           "/interfaces-ios-xe-oper:interfaces/interface"
         ]
       },
       {
         "name": "Cisco-IOS-XE-memory-oper",
         "organization": "Cisco Systems, Inc.",
         "version": "2019-01-16",
         "category": "memory"
       }
     ]
   }

2. Known model → category → subscription path mapping:
   Maintain a registry of known YANG models:
   
   YANG_MODEL_REGISTRY = {
     # Cisco IOS-XE
     "Cisco-IOS-XE-interfaces-oper": {
       "category": "interfaces",
       "description": "Interface operational data",
       "paths": ["/interfaces-ios-xe-oper:interfaces/interface"],
       "metrics": ["in-octets", "out-octets", "rx-kbps", "tx-kbps"]
     },
     "Cisco-IOS-XE-memory-oper": {
       "category": "memory",
       "description": "Memory statistics",
       "paths": ["/memory-ios-xe-oper:memory-statistics/memory-statistic"],
       "metrics": ["free-memory", "used-memory", "total-memory"]
     },
     "Cisco-IOS-XE-process-cpu-oper": {
       "category": "cpu",
       "description": "CPU utilization",
       "paths": ["/process-cpu-ios-xe-oper:cpu-usage/cpu-utilization"],
       "metrics": ["five-seconds", "one-minute", "five-minutes"]
     },
     "Cisco-IOS-XE-environment-oper": {
       "category": "environment",
       "description": "Temperature, fans, power",
       "paths": ["/environment-ios-xe-oper:environment-sensors/environment-sensor"],
       "metrics": ["current-reading", "sensor-status"]
     },
     "Cisco-IOS-XE-bgp-oper": {
       "category": "bgp",
       "description": "BGP neighbor state",
       "paths": ["/bgp-ios-xe-oper:bgp-state-data/neighbors/neighbor"],
       "metrics": ["session-state", "prefixes-received"]
     },
     "Cisco-IOS-XE-poe-oper": {
       "category": "poe",
       "description": "Power over Ethernet",
       "paths": ["/poe-ios-xe-oper:poe-data"],
       "metrics": ["power-used", "power-class", "poe-enabled"]
     },
     
     # OpenConfig (vendor-neutral)
     "openconfig-interfaces": {
       "category": "interfaces",
       "description": "OpenConfig interfaces",
       "paths": ["/interfaces/interface"],
       "metrics": ["in-octets", "out-octets", "in-errors"]
     },
     "openconfig-bgp": {
       "category": "bgp",
       "description": "OpenConfig BGP",
       "paths": ["/network-instances/network-instance/protocols/protocol/bgp/neighbors/neighbor"],
       "metrics": ["session-state", "prefixes-received"]
     },
     "openconfig-system": {
       "category": "system",
       "description": "System CPU and memory",
       "paths": ["/system/cpus/cpu", "/system/memory/state"],
       "metrics": ["instant", "avg", "max"]
     },
     
     # Juniper
     "junos-interface-common": {
       "category": "interfaces",
       "description": "Juniper interface data",
       "paths": ["/interfaces/interface"],
       "metrics": ["statistics"]
     },
     
     # Arista
     "arista-intf-augments": {
       "category": "interfaces", 
       "description": "Arista interface extensions",
       "paths": ["/interfaces/interface"],
       "metrics": ["in-octets", "out-octets"]
     },
   }

3. Subscription selector UI:
   
   In Telemetry Configuration slide-over,
   add "gNMI Subscriptions" section:
   
   [Discover Capabilities] button
   → Calls /api/devices/{id}/gnmi/capabilities/
   → Shows what device supports
   
   After discovery, show categorized checkboxes:
   
   ┌─────────────────────────────────────────────┐
   │ gNMI Subscriptions          [Discover ↻]   │
   │ Device supports 12 YANG models              │
   ├─────────────────────────────────────────────┤
   │ ☑ Interfaces (per-interface)               │
   │   Cisco-IOS-XE-interfaces-oper             │
   │   Interval: [30s ▼]                        │
   │   Interfaces: [GigabitEthernet1 ×] [+Add]  │
   ├─────────────────────────────────────────────┤
   │ ☑ CPU Utilization                          │
   │   Cisco-IOS-XE-process-cpu-oper            │
   │   Interval: [30s ▼]                        │
   ├─────────────────────────────────────────────┤
   │ ☑ Memory Statistics                        │
   │   Cisco-IOS-XE-memory-oper                 │
   │   Interval: [30s ▼]                        │
   ├─────────────────────────────────────────────┤
   │ ☑ Environment (temp/fan/power)             │
   │   Cisco-IOS-XE-environment-oper            │
   │   Interval: [60s ▼]                        │
   ├─────────────────────────────────────────────┤
   │ ☐ BGP Neighbors                            │
   │   Cisco-IOS-XE-bgp-oper                    │
   │   Interval: [100s ▼]                       │
   ├─────────────────────────────────────────────┤
   │ ☐ POE (Power over Ethernet)                │
   │   Cisco-IOS-XE-poe-oper                    │
   │   Interval: [30s ▼]                        │
   ├─────────────────────────────────────────────┤
   │ ⚪ MPLS (not supported on this device)      │
   │   Cisco-IOS-XE-mpls-oper (not found)       │
   └─────────────────────────────────────────────┘
   
   [Generate Config] → updates gNMI snippet
   based on selected subscriptions + intervals

4. Store subscription preferences:
   New model: DeviceGNMISubscription
   device: FK(Device)
   yang_model: CharField
   category: CharField
   enabled: BooleanField
   interval_seconds: IntegerField
   custom_paths: ArrayField (override defaults)
   
   Used by telemetry config generator to produce
   targeted subscription config.

5. Config generation uses selections:
   Only generate subscriptions for:
   - Enabled categories
   - At specified intervals
   - With device-supported paths
   
   Replaces current hardcoded subscription list.

6. gNMI connection for capabilities:
   Use existing ingest-grpc infrastructure
   OR connect directly from api service
   using grpcio + gnmi_pb2
   
   Connection: device.management_ip : 57400
   TLS: optional (check device cert or skip verify)
   Auth: device credential profile
   
   Note: capabilities don't require auth on most
   devices but may require TLS.

7. Fallback when capabilities not available:
   If device doesn't respond to Capabilities():
   Show platform-default subscriptions
   (current hardcoded behavior)
   Mark as "Platform defaults (capabilities 
   not available)"

### Benefits:
- No more wrong OIDs for platform versions
- Engineers see exactly what device supports
- Selective subscription = less device load
- Automatic when new YANG models available
- Works across all vendors using gNMI

### Connection options:
  Port 57400: standard gNMI (Cisco MDT)
  Port 6030: Arista gNMI
  Port 32767: some Juniper implementations
  Store preferred port in TelemetryConfig

### Do NOT build until requested.
### Priority: High - makes gNMI setup much easier
### Depends on: gNMI dial-in capability
###   (current ingest-grpc is dial-OUT only)
###   Need to add dial-IN gNMI client to api service

## PINNED — Device Data Collection Method Display

### Purpose:
Show engineers exactly HOW data is being collected
for each device at a glance. Removes guesswork about
whether gNMI streaming or SNMP polling is active.

### Locations to show collection method:

1. Device detail header (next to IP/SSH button):
   Show active collection badges:
   
   "📡 gNMI" (green, streaming active)
   "📊 SNMP" (blue, polling active)
   "📡 gNMI + 📊 SNMP" (both active)
   "⚠️ No telemetry" (yellow, neither active)
   
   Tooltip on hover:
   📡 gNMI: "Streaming telemetry active
              Last message: 15 seconds ago
              294 metrics/push · every 30s
              Subscriptions: 6 active"
   
   📊 SNMP: "SNMP polling active
              Last poll: 2 minutes ago
              26 OIDs · every 300s
              Version: SNMPv3 authPriv"

2. Telemetry tab Device Health section:
   Add collection method indicator:
   
   Current:  [1h][6h][24h][7d] [Poll Now] [Configure →]
   New:      [1h][6h][24h][7d] [Poll Now] [Configure →]
             📡 gNMI streaming · 📊 SNMP polling
   
   Or as a status bar below time selector:
   ┌────────────────────────────────────────────┐
   │ 📡 gNMI active — 294 metrics/30s          │
   │ 📊 SNMP active — 26 OIDs/300s             │
   └────────────────────────────────────────────┘

3. Device list table:
   Optional column "Telemetry" (in column picker):
   Shows icons: 📡 📊 or ⚠️
   
4. Device Overview tab:
   In the quick stats section:
   "Collection: 📡 gNMI + 📊 SNMP"

### Logic to determine collection status:

gNMI active:
  Check Valkey key: gnmi:last_seen:{device_id}
  If exists and < 120s ago → gNMI active
  Show: last message time, metrics count if available

SNMP active:
  Check InfluxDB for recent poll_duration_ms:
  from(bucket:"metrics")
    |> range(start: -10m)
    |> filter(fn: (r) => r.device_id == "{id}")
    |> filter(fn: (r) => r._field == "poll_duration_ms")
    |> last()
  If record exists → SNMP active
  Show: last poll time, OID count

Neither active:
  Show warning with link to configure:
  "⚠️ No telemetry configured
   [Configure in Telemetry Settings →]"

### API:
GET /api/devices/{id}/collection-status/
Returns:
{
  "gnmi": {
    "active": true,
    "last_seen": "2026-05-31T18:30:00Z",
    "seconds_ago": 15,
    "metrics_per_push": 294,
    "interval_seconds": 30,
    "subscriptions": 6
  },
  "snmp": {
    "active": true,
    "last_poll": "2026-05-31T18:28:00Z",
    "seconds_ago": 120,
    "oid_count": 26,
    "interval_seconds": 300,
    "version": "v3"
  },
  "primary": "gnmi",  ← which is preferred/active
  "any_active": true
}

### Adaptive polling indicator:
When gNMI/SNMP adaptive polling is implemented
(pinned separately in CLAUDE.md):
Show: "📡 gNMI active — SNMP device metrics suppressed"
This tells engineers the system is working as designed.

### Do NOT build until requested.

## PINNED — gNMI Platform Profiles

### Extension of: gNMI Capability Discovery pin above

### Problem:
When you have 50 Cisco Catalyst 9300s, you don't want
to run capability discovery on every single one.
They all support the same YANG models.
Define once, apply to all devices of that platform.

### gNMI Platform Profile:
A reusable subscription template per vendor/platform.
Run capability discovery once → save as profile →
apply to all matching devices automatically.

Model: GNMIProfile
  name: CharField (e.g. "Cisco Catalyst 9300")
  description: TextField
  vendor: CharField (cisco/juniper/arista/etc)
  platform: CharField (ios_xe/eos/junos/etc)
  platform_version: CharField (null=all, "17.x"=specific)
  is_default: BooleanField
    ← auto-apply to new devices of this platform
  created_from_device: FK(Device, null=True)
    ← which device was used for capability discovery
  discovered_at: DateTimeField(null=True)
  created_by: FK(User)
  
  # Capabilities discovered
  gnmi_version: CharField
  supported_encodings: ArrayField
  supported_models: JSONField
    ← full list from CapabilityResponse
  
  # Subscription configuration
  subscriptions: JSONField
  ← [
      {
        "category": "cpu",
        "yang_model": "Cisco-IOS-XE-process-cpu-oper",
        "path": "/process-cpu-ios-xe-oper:cpu-usage/cpu-utilization",
        "interval_seconds": 30,
        "enabled": true,
        "encoding": "encode-kvgpb"
      },
      {
        "category": "memory",
        "yang_model": "Cisco-IOS-XE-memory-oper", 
        "path": "/memory-ios-xe-oper:memory-statistics/memory-statistic",
        "interval_seconds": 30,
        "enabled": true
      },
      {
        "category": "environment",
        "yang_model": "Cisco-IOS-XE-environment-oper",
        "path": "/environment-ios-xe-oper:environment-sensors/environment-sensor",
        "interval_seconds": 60,
        "enabled": true
      },
      {
        "category": "bgp",
        "yang_model": "Cisco-IOS-XE-bgp-oper",
        "path": "/bgp-ios-xe-oper:bgp-state-data/neighbors/neighbor",
        "interval_seconds": 100,
        "enabled": false  ← off by default, enable if BGP device
      },
      {
        "category": "interfaces",
        "yang_model": "Cisco-IOS-XE-interfaces-oper",
        "path": "/interfaces-ios-xe-oper:interfaces/interface[name='{if_name}']",
        "interval_seconds": 30,
        "enabled": true,
        "per_interface": true  ← generates one sub per monitored interface
      }
    ]

### Built-in Default Profiles:
Ship NetPulse with pre-built profiles for common platforms.
Engineers can use immediately without capability discovery.

seed_gnmi_profiles management command:

1. Cisco IOS-XE (Default)
   Based on C8000V/CSR1000v/Catalyst 8000
   Tested and verified in lab ✅
   Subscriptions: CPU, Memory, Environment, BGP, Interfaces
   
2. Cisco IOS-XE Catalyst (Switches)
   Adds: POE, Stack, VLANs
   Removes: BGP (not typical on access switches)

3. Cisco IOS-XR
   NCS, ASR 9000 series
   Uses IOS-XR specific YANG models
   
4. Cisco NX-OS
   Nexus switches
   Uses NX-OS specific paths

5. Juniper JunOS (OpenConfig)
   Uses OpenConfig models where possible
   Native Juniper models as fallback

6. Arista EOS
   OpenConfig based
   TerminAttr agent required
   Port: 6030 (default for Arista)

7. Generic OpenConfig
   Vendor-neutral fallback
   Works on any OpenConfig-compliant device
   May not have full coverage

### Profile Workflow:

Option A - Use built-in profile:
  Settings → gNMI Profiles → Select platform
  → Apply to device(s)
  → Generate config
  Done.

Option B - Discover from device:
  Device → Telemetry → [Discover Capabilities]
  → Review discovered models
  → Enable/disable/set intervals
  → [Save as Profile]
  → Name: "My Catalyst 9300 Profile"
  → Platform: ios_xe
  → [Save]
  Now appears in profile list for reuse.

Option C - Clone and customize:
  Take built-in profile → Clone → Modify
  e.g. "Cisco IOS-XE - BGP Enabled"
  = default profile + BGP subscription enabled

### Applying profiles to devices:

1. Individual device:
   Device → Settings → Telemetry Configuration
   [Select gNMI Profile ▼]
   Shows: platform-matching profiles first
   Apply → regenerates gNMI subscription config

2. Bulk apply:
   Settings → gNMI Profiles → {Profile}
   → [Apply to Devices]
   Multi-select devices (filtered by platform)
   → Apply profile to all selected
   → Regenerate configs for all

3. Auto-apply on device add:
   If profile has is_default=True for platform:
   When new device added with matching platform
   → Auto-assign default profile
   → Show in wizard Step 4 telemetry config

4. Profile inheritance:
   Device can override individual subscriptions
   Base: platform profile
   Override: device-specific intervals/paths
   
   Display: "Using Cisco IOS-XE profile
             + 2 device-specific overrides"

### Profile comparison:
Show diff between two profiles:
  ┌──────────────────────┬─────────────┬──────────────┐
  │ Subscription         │ Profile A   │ Profile B    │
  ├──────────────────────┼─────────────┼──────────────┤
  │ CPU                  │ 30s ✅      │ 30s ✅       │
  │ Memory               │ 30s ✅      │ 60s ⚠️ diff  │
  │ Environment          │ 60s ✅      │ ❌ disabled  │
  │ BGP                  │ ❌ disabled │ 100s ✅      │
  │ Interfaces           │ 30s ✅      │ 30s ✅       │
  │ POE                  │ ❌ disabled │ ❌ disabled  │
  └──────────────────────┴─────────────┴──────────────┘

### API Endpoints:
GET  /api/gnmi/profiles/
POST /api/gnmi/profiles/
GET  /api/gnmi/profiles/{id}/
PUT  /api/gnmi/profiles/{id}/
DELETE /api/gnmi/profiles/{id}/
POST /api/gnmi/profiles/{id}/clone/
POST /api/gnmi/profiles/{id}/apply/
  Body: {device_ids: [1, 2, 3]}
GET  /api/gnmi/profiles/defaults/
  Returns default profile per platform

POST /api/devices/{id}/gnmi/apply-profile/
  Body: {profile_id: 1}

### Settings UI:
Settings → Telemetry → gNMI Profiles

Profile list:
┌─────────────────────────────────────────────────────┐
│ gNMI Profiles              [+ New Profile]          │
├─────────────────────────────────────────────────────┤
│ 🔒 Cisco IOS-XE (Default)     ios_xe  6 subs  ✅   │
│    Applied to: 2 devices                            │
│    [Clone] [Apply to Devices]                       │
├─────────────────────────────────────────────────────┤
│ 🔒 Cisco IOS-XE Catalyst      ios_xe  8 subs       │
│    Applied to: 0 devices                            │
│    [Clone] [Apply to Devices]                       │
├─────────────────────────────────────────────────────┤
│ 🔒 Arista EOS                 eos     5 subs        │
│    [Clone] [Apply to Devices]                       │
├─────────────────────────────────────────────────────┤
│ ✏️  My BGP Router Profile      ios_xe  7 subs       │
│    Created from router1 · May 31 2026               │
│    Applied to: 0 devices                            │
│    [Edit] [Clone] [Apply to Devices] [Delete]       │
└─────────────────────────────────────────────────────┘

### Versioning:
Track profile versions when subscriptions change:
  profile.version: IntegerField (auto-increment)
  profile.changelog: JSONField
    [{version: 2, changed: "Added POE", date: ...}]
  
  Devices track which profile version they use:
  DeviceTelemetryConfig.profile_version: IntegerField
  
  Show warning when device uses outdated profile:
  "⚠️ Profile updated — device uses v1, current is v2
   [Update to latest]"

### Do NOT build until requested.
### Priority: High - essential for fleet management
### Depends on: gNMI Capability Discovery pin above

## PINNED — gNMI Capability Discovery & Selective Subscriptions

### Vision:
Instead of hardcoding subscription paths per platform,
query the device's gNMI capabilities endpoint to discover
what YANG models and paths are actually supported.
Engineers then selectively choose what to subscribe to.

### gNMI Capabilities RPC:
gNMI spec defines a Capabilities() RPC:
  Request:  CapabilityRequest {}
  Response: CapabilityResponse {
    supported_models: [ModelData]
    supported_encodings: [Encoding]
    gNMI_version: string
  }
  
  ModelData {
    name: string      (e.g. "Cisco-IOS-XE-interfaces-oper")
    organization: str (e.g. "Cisco Systems, Inc.")
    version: string   (e.g. "2022-11-01")
  }

### Implementation:

1. New API endpoint:
   POST /api/devices/{id}/gnmi/capabilities/
   
   Connects to device gNMI port (57400 or 6030 for Arista)
   Sends CapabilityRequest
   Returns list of supported YANG models + encodings
   
   Response:
   {
     "gnmi_version": "0.7.0",
     "supported_encodings": ["JSON_IETF", "PROTO"],
     "models": [
       {
         "name": "Cisco-IOS-XE-interfaces-oper",
         "organization": "Cisco Systems, Inc.",
         "version": "2022-11-01",
         "category": "interfaces",  ← derived
         "paths": [                  ← known paths for this model
           "/interfaces-ios-xe-oper:interfaces",
           "/interfaces-ios-xe-oper:interfaces/interface"
         ]
       },
       {
         "name": "Cisco-IOS-XE-memory-oper",
         "organization": "Cisco Systems, Inc.",
         "version": "2019-01-16",
         "category": "memory"
       }
     ]
   }

2. Known model → category → subscription path mapping:
   Maintain a registry of known YANG models:
   
   YANG_MODEL_REGISTRY = {
     # Cisco IOS-XE
     "Cisco-IOS-XE-interfaces-oper": {
       "category": "interfaces",
       "description": "Interface operational data",
       "paths": ["/interfaces-ios-xe-oper:interfaces/interface"],
       "metrics": ["in-octets", "out-octets", "rx-kbps", "tx-kbps"]
     },
     "Cisco-IOS-XE-memory-oper": {
       "category": "memory",
       "description": "Memory statistics",
       "paths": ["/memory-ios-xe-oper:memory-statistics/memory-statistic"],
       "metrics": ["free-memory", "used-memory", "total-memory"]
     },
     "Cisco-IOS-XE-process-cpu-oper": {
       "category": "cpu",
       "description": "CPU utilization",
       "paths": ["/process-cpu-ios-xe-oper:cpu-usage/cpu-utilization"],
       "metrics": ["five-seconds", "one-minute", "five-minutes"]
     },
     "Cisco-IOS-XE-environment-oper": {
       "category": "environment",
       "description": "Temperature, fans, power",
       "paths": ["/environment-ios-xe-oper:environment-sensors/environment-sensor"],
       "metrics": ["current-reading", "sensor-status"]
     },
     "Cisco-IOS-XE-bgp-oper": {
       "category": "bgp",
       "description": "BGP neighbor state",
       "paths": ["/bgp-ios-xe-oper:bgp-state-data/neighbors/neighbor"],
       "metrics": ["session-state", "prefixes-received"]
     },
     "Cisco-IOS-XE-poe-oper": {
       "category": "poe",
       "description": "Power over Ethernet",
       "paths": ["/poe-ios-xe-oper:poe-data"],
       "metrics": ["power-used", "power-class", "poe-enabled"]
     },
     
     # OpenConfig (vendor-neutral)
     "openconfig-interfaces": {
       "category": "interfaces",
       "description": "OpenConfig interfaces",
       "paths": ["/interfaces/interface"],
       "metrics": ["in-octets", "out-octets", "in-errors"]
     },
     "openconfig-bgp": {
       "category": "bgp",
       "description": "OpenConfig BGP",
       "paths": ["/network-instances/network-instance/protocols/protocol/bgp/neighbors/neighbor"],
       "metrics": ["session-state", "prefixes-received"]
     },
     "openconfig-system": {
       "category": "system",
       "description": "System CPU and memory",
       "paths": ["/system/cpus/cpu", "/system/memory/state"],
       "metrics": ["instant", "avg", "max"]
     },
     
     # Juniper
     "junos-interface-common": {
       "category": "interfaces",
       "description": "Juniper interface data",
       "paths": ["/interfaces/interface"],
       "metrics": ["statistics"]
     },
     
     # Arista
     "arista-intf-augments": {
       "category": "interfaces", 
       "description": "Arista interface extensions",
       "paths": ["/interfaces/interface"],
       "metrics": ["in-octets", "out-octets"]
     },
   }

3. Subscription selector UI:
   
   In Telemetry Configuration slide-over,
   add "gNMI Subscriptions" section:
   
   [Discover Capabilities] button
   → Calls /api/devices/{id}/gnmi/capabilities/
   → Shows what device supports
   
   After discovery, show categorized checkboxes:
   
   ┌─────────────────────────────────────────────┐
   │ gNMI Subscriptions          [Discover ↻]   │
   │ Device supports 12 YANG models              │
   ├─────────────────────────────────────────────┤
   │ ☑ Interfaces (per-interface)               │
   │   Cisco-IOS-XE-interfaces-oper             │
   │   Interval: [30s ▼]                        │
   │   Interfaces: [GigabitEthernet1 ×] [+Add]  │
   ├─────────────────────────────────────────────┤
   │ ☑ CPU Utilization                          │
   │   Cisco-IOS-XE-process-cpu-oper            │
   │   Interval: [30s ▼]                        │
   ├─────────────────────────────────────────────┤
   │ ☑ Memory Statistics                        │
   │   Cisco-IOS-XE-memory-oper                 │
   │   Interval: [30s ▼]                        │
   ├─────────────────────────────────────────────┤
   │ ☑ Environment (temp/fan/power)             │
   │   Cisco-IOS-XE-environment-oper            │
   │   Interval: [60s ▼]                        │
   ├─────────────────────────────────────────────┤
   │ ☐ BGP Neighbors                            │
   │   Cisco-IOS-XE-bgp-oper                    │
   │   Interval: [100s ▼]                       │
   ├─────────────────────────────────────────────┤
   │ ☐ POE (Power over Ethernet)                │
   │   Cisco-IOS-XE-poe-oper                    │
   │   Interval: [30s ▼]                        │
   ├─────────────────────────────────────────────┤
   │ ⚪ MPLS (not supported on this device)      │
   │   Cisco-IOS-XE-mpls-oper (not found)       │
   └─────────────────────────────────────────────┘
   
   [Generate Config] → updates gNMI snippet
   based on selected subscriptions + intervals

4. Store subscription preferences:
   New model: DeviceGNMISubscription
   device: FK(Device)
   yang_model: CharField
   category: CharField
   enabled: BooleanField
   interval_seconds: IntegerField
   custom_paths: ArrayField (override defaults)
   
   Used by telemetry config generator to produce
   targeted subscription config.

5. Config generation uses selections:
   Only generate subscriptions for:
   - Enabled categories
   - At specified intervals
   - With device-supported paths
   
   Replaces current hardcoded subscription list.

6. gNMI connection for capabilities:
   Use existing ingest-grpc infrastructure
   OR connect directly from api service
   using grpcio + gnmi_pb2
   
   Connection: device.management_ip : 57400
   TLS: optional (check device cert or skip verify)
   Auth: device credential profile
   
   Note: capabilities don't require auth on most
   devices but may require TLS.

7. Fallback when capabilities not available:
   If device doesn't respond to Capabilities():
   Show platform-default subscriptions
   (current hardcoded behavior)
   Mark as "Platform defaults (capabilities 
   not available)"

### Benefits:
- No more wrong OIDs for platform versions
- Engineers see exactly what device supports
- Selective subscription = less device load
- Automatic when new YANG models available
- Works across all vendors using gNMI

### Connection options:
  Port 57400: standard gNMI (Cisco MDT)
  Port 6030: Arista gNMI
  Port 32767: some Juniper implementations
  Store preferred port in TelemetryConfig

### Do NOT build until requested.
### Priority: High - makes gNMI setup much easier
### Depends on: gNMI dial-in capability
###   (current ingest-grpc is dial-OUT only)
###   Need to add dial-IN gNMI client to api service

## System Service (Auto-start on Boot)

NetPulse runs as a systemd service:

  sudo systemctl start netpulse    # start
  sudo systemctl stop netpulse     # stop
  sudo systemctl restart netpulse  # restart
  sudo systemctl status netpulse   # check status
  sudo systemctl enable netpulse   # enable on boot
  sudo systemctl disable netpulse  # disable on boot

Service file: /etc/systemd/system/netpulse.service
Requires: docker.service
WorkingDirectory: /home/netmagic/netpulse

After reboot:
  Services start automatically via docker compose up -d
  Run ./scripts/setup.sh only if OpenBao was wiped
  (factory reset or volume deletion)

## Docker NAT (Required)

NetPulse containers must NAT to the host IP for SNMP/SSH to work with devices
that restrict access by source IP. Applied automatically by `setup.sh`:

  sudo iptables -t nat -A POSTROUTING \
    -s {docker_subnet} \
    ! -d {docker_subnet} \
    -j MASQUERADE

This ensures:
- All container traffic appears to come from the host IP
- No Docker subnet conflicts with the network
- SNMP/SSH works regardless of device ACLs

If SNMP stops working after reboot:  `sudo ./netpulse.sh fix-nat`
(applying the rule needs root — run the whole command under sudo so the script
skips its inner sudo.)

### Details

- Docker containers always NAT to the host IP (MASQUERADE on the netpulse
  bridge subnet, default 172.18.0.0/16 — the network is `netpulse_netpulse-net`,
  not `netpulse_default`).
- Why: devices that filter SNMP/SSH by source IP see the host IP (not a
  container IP), and the 172.x bridge range can't collide with real network
  infrastructure. No per-deployment decision needed.
- Applied by `scripts/setup.sh` after the stack starts, and re-applied during
  `scripts/update.sh`. Shared logic lives in `scripts/nat.sh`
  (`apply_docker_nat` / `detect_docker_subnet`), idempotent.
- Persisted across reboots via netfilter-persistent (or
  `/etc/iptables/rules.v4`); if iptables-persistent isn't installed the rule is
  lost on reboot.
- If SNMP/SSH from containers stops working after a reboot:
    sudo ./netpulse.sh fix-nat
  (requires root/sudo — iptables needs privileges; run the whole command with
  sudo so the script skips its inner sudo.)
- Health check: `run_health_checks` includes a "Docker NAT" check. It runs
  inside the api container, which has no host iptables access, so it WARNs
  ("verify on the host") rather than failing; run `fix-nat` on the host to apply.

## PINNED — AOS-CX and Aruba Telemetry + Config Templates

### Next session starts here.

### HPE AOS-CX:

Config push template (SSH):
  Needs Netmiko device_type: 'aruba_aoscx'
  Or use REST API (AOS-CX has full REST API on port 443)
  
  SNMP config snippet:
  snmp-server vrf mgmt
  snmp-server community netpulse
  snmpv3 user testsnmp auth sha auth-pass netmagic \
    priv aes priv-pass netmagic
  
  Syslog config snippet:
  logging <collector_ip> severity info
  
  gNMI: AOS-CX supports OpenConfig gNMI
    Port: 8443 (not 57400)
    Uses OpenConfig YANG models
    TerminAttr NOT needed (native gNMI support)
  
  OpenConfig paths for AOS-CX:
    CPU:        /system/cpus/cpu[index=0]/state/usage
    Memory:     /system/memory/state
    Interfaces: /interfaces/interface/state/counters
    BGP:        /network-instances/network-instance/
                protocols/protocol/bgp/neighbors/neighbor

### Aruba AOS (Mobility Controllers):

Config push template:
  Netmiko device_type: 'aruba_os' or 'aruba_osswitch'
  
  SNMP config:
  snmp-server community netpulse
  snmp-server enable trap
  
  Syslog:
  logging <collector_ip>
  
  gNMI: NOT supported on AOS mobility controllers
  Use SNMP + Syslog only
  
  Key OIDs already configured:
  wlsxSysXCpuUtilization: 1.3.6.1.4.1.14823.2.2.1.1.1.11.0
  wlsxSysXMemoryUsage:    1.3.6.1.4.1.14823.2.2.1.1.1.10.0
  wlsxTotalNumAPs:        1.3.6.1.4.1.14823.2.2.1.1.3.3.0
  wlsxTotalNumClients:    1.3.6.1.4.1.14823.2.2.1.1.3.4.0

### Tasks for next session:

1. Add AOS-CX to telemetry config generator:
   - SNMP snippet (v3 preferred)
   - Syslog snippet
   - gNMI snippet (OpenConfig, port 8443)
   - Show "AOS-CX supports native gNMI on port 8443"

2. Add Aruba AOS to telemetry config generator:
   - SNMP snippet
   - Syslog snippet
   - Note: no gNMI support

3. Update ingest-grpc for AOS-CX gNMI:
   - AOS-CX uses port 8443 (not 57400)
   - Uses standard gNMI dial-out (not Cisco MDT)
   - OpenConfig field names differ from Cisco

4. Test with real AOS-CX and Aruba hardware
   in remote lab

5. Config push via Netmiko:
   - AOS-CX: test 'aruba_aoscx' device_type
   - Aruba: test 'aruba_os' device_type
   - May need REST API for AOS-CX instead of SSH

### Do NOT build until next session.

## PINNED — Aruba Central Integration (AOS-CX)

### Background:
Aruba Central is HPE's cloud management platform for
AOS-CX switches and Aruba APs. When Central is in use:
- Device config is managed via Central (not SSH directly)
- Config push should go via Central API not SSH
- Telemetry may flow via Central not directly to NetPulse
- SSH may still work but config changes should use Central

### New Device Setting:
Add to Device model or DeviceTelemetryConfig:

aruba_central_enabled: BooleanField(default=False)
aruba_central_device_id: CharField(null=True)
  ← Device ID in Aruba Central

### Aruba Central API:
Base URL: https://apigw-prod2.central.arubanetworks.com
Auth: OAuth2 client credentials

Required .env when Central enabled:
ARUBA_CENTRAL_CLIENT_ID=
ARUBA_CENTRAL_CLIENT_SECRET=
ARUBA_CENTRAL_CUSTOMER_ID=
ARUBA_CENTRAL_BASE_URL=https://apigw-prod2.central.arubanetworks.com

### UI Setting:
In Device Settings → Edit Device or Telemetry Config:

┌─────────────────────────────────────────────────┐
│ Management Mode                                 │
│ ○ Direct (SSH + SNMP)                          │
│ ● Aruba Central managed                         │
│   Central Device ID: [abc123        ]           │
│   [Test Central Connection]                     │
└─────────────────────────────────────────────────┘

### Behavior when Central enabled:

Config Collection:
  Use Central API instead of SSH:
  GET /monitoring/v1/switches/{device_id}/config
  
Config Push:
  Use Central API instead of SSH:
  POST /configuration/v1/devices/{device_id}/config
  Show warning: "Config will be pushed via Aruba Central"

Telemetry:
  Central can stream telemetry via webhooks
  OR direct SNMP/gNMI still works if accessible
  
  If direct access available: use SNMP/gNMI directly
  If behind Central only: use Central streaming API

SNMP:
  Usually still accessible directly even with Central
  Use standard SNMP polling as normal

### Central API endpoints useful for NetPulse:
GET  /monitoring/v1/switches          ← list devices
GET  /monitoring/v1/switches/{id}     ← device details
GET  /monitoring/v1/switches/{id}/ports ← interface stats
GET  /monitoring/v1/switches/{id}/config ← running config
POST /configuration/v1/devices/{id}/config ← push config
GET  /monitoring/v1/alerts            ← Central alerts

### When to show Central option in UI:
Only show Aruba Central settings when:
  device.platform == 'aos_cx' OR device.platform == 'aruba'
  
For other platforms: hide Central settings entirely.

### Do NOT build until next session.
### Build AFTER basic AOS-CX telemetry templates.
### Priority: Medium (after direct telemetry works)

## PINNED — AOS-CX Central Managed Config Push Pattern

### Important: Config push on Central-managed AOS-CX

When Aruba Central is managing an AOS-CX switch,
direct SSH config push requires temporarily
disabling Central management first.

### Required sequence for config push:

Step 1: Disable Central management via SSH
  aruba-central disable

Step 2: WAIT 2 seconds (critical!)
  time.sleep(2)
  ← Changes often fail without this delay
  ← Central needs time to release control

Step 3: Push config changes via SSH
  (normal Netmiko config push)

Step 4: Re-enable Central management
  aruba-central enable

Step 5: Verify Central reconnects
  show aruba-central

### Implementation in config push:

def push_config_aos_cx_central(device, commands):
    with netmiko.ConnectHandler(**ssh_params) as conn:
        
        # Step 1: Disable Central
        conn.send_command('aruba-central disable')
        logger.info(f"Central disabled on {device.hostname}")
        
        # Step 2: Critical delay
        time.sleep(2)
        
        # Step 3: Push changes
        output = conn.send_config_set(commands)
        conn.save_config()  # write memory
        logger.info(f"Config pushed to {device.hostname}")
        
        # Step 4: Re-enable Central
        conn.send_command('aruba-central enable')
        logger.info(f"Central re-enabled on {device.hostname}")
        
        # Step 5: Verify
        status = conn.send_command('show aruba-central')
        logger.info(f"Central status: {status[:100]}")
        
        return output

### Error handling:

If push fails after disabling Central:
  MUST still re-enable Central before raising exception
  Use try/finally:
  
  try:
      conn.send_command('aruba-central disable')
      time.sleep(2)
      output = conn.send_config_set(commands)
      conn.save_config()
  except Exception as e:
      logger.error(f"Config push failed: {e}")
      raise
  finally:
      # Always re-enable Central even if push failed
      try:
          conn.send_command('aruba-central enable')
      except Exception:
          logger.error("CRITICAL: Failed to re-enable Central!")
          # Alert engineer - switch stuck in non-Central mode

### UI Warning when Central enabled + config push:

Show before pushing:
┌─────────────────────────────────────────────────┐
│ ⚠️  Aruba Central Managed Device                │
│                                                 │
│ Config push will:                               │
│ 1. Temporarily disable Aruba Central (2s)       │
│ 2. Push configuration via SSH                   │
│ 3. Re-enable Aruba Central                      │
│                                                 │
│ Device will briefly lose Central management.    │
│ Do not interrupt this process.                  │
│                                                 │
│ [Cancel]  [Proceed with Push]                   │
└─────────────────────────────────────────────────┘

### Also applies to:
- Compliance remediation pushes
- Telemetry subscription config push
- Any automated config changes

### Do NOT build until next session.
### This pattern is REQUIRED for Central-managed AOS-CX.
### The 2-second delay is non-negotiable.

## PINNED — AOS-CX Device Enrichment

### When an AOS-CX device is approved from discovery
or added manually, enrichment should collect:

### SNMP Enrichment (standard + AOS-CX specific):

sysDescr:     1.3.6.1.2.1.1.1.0
  → Parse: "ArubaOS-CX 10.10.1010" → os_version
  → Parse: "6300M" or "6400" → model

sysObjectID:  1.3.6.1.2.1.1.2.0
  → Map to model name:
  1.3.6.1.4.1.47196.4.1.1.3.8 → Aruba 6300M
  1.3.6.1.4.1.47196.4.1.1.3.9 → Aruba 6400

entPhysDescr: 1.3.6.1.2.1.47.1.1.1.1.2.1
  → Physical description (chassis/module info)

entSerialNum: 1.3.6.1.2.1.47.1.1.1.1.11.1
  → Serial number

### REST API Enrichment (preferred for AOS-CX):

AOS-CX has full REST API on port 443:
Base: https://{device_ip}/rest/v10.09/

Auth: Basic auth or token
  POST /rest/v10.09/login
  username/password from credential profile

Endpoints useful for enrichment:
  GET /rest/v10.09/system
    → hostname, software_version, hardware_info
    → serial_number, product_name (model)
  
  GET /rest/v10.09/system/interfaces
    → all interfaces with stats
  
  GET /rest/v10.09/system/vrfs/default/bgp_routers
    → BGP config if present

REST API response example:
{
  "hostname": "core-sw-1",
  "software_version": "FL.10.10.1010",
  "hardware_info": {
    "product_name": "Aruba6300M-48G-Class4PoEP-4SFP56"
  },
  "serial_number": "SGxxxxxxxxxx"
}

### Enrichment priority:
1. Try REST API first (most accurate)
2. Fall back to SNMP if REST unavailable
3. Fall back to SSH "show version" if both fail

### SSH enrichment (fallback):
Netmiko device_type: 'aruba_aoscx'

Command: show version
Output parsing:
  "ArubaOS-CX 10.10.1010" → os_version
  "Aruba6300M" → model
  "Serial Number: SGxx" → serial

### sysDescr parsing patterns for AOS-CX:
"ArubaOS-CX" → platform = aos_cx ✅ (already done)
Version regex: r'ArubaOS-CX\s+([\d\.]+)'
Model regex: r'(Aruba\d+[A-Z]?)' or from sysObjectID

### Interface discovery for AOS-CX:
Use REST API:
GET /rest/v10.09/system/interfaces?depth=2

Returns all interfaces with:
- name, type, admin_state, link_state
- ip_address, description
- Much faster than SSH for large switches

OR use SNMP ifTable (standard, already works)

### LLDP for AOS-CX:
REST API:
GET /rest/v10.09/system/lldp_neighbors_info

Returns LLDP neighbor table directly.
Faster and more reliable than SSH parsing.

### AOS-CX REST API credential storage:
Store in OpenBao alongside SSH:
secret/devices/{uuid}/aos_cx_rest:
  username: admin
  password: ****
  verify_ssl: false  ← self-signed cert common

OR reuse SSH credentials (same username/password
usually works for both SSH and REST API)

### Do NOT build until next session.
### Priority: High - needed for remote lab testing
