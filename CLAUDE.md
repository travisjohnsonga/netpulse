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
- services/api check-engine: agentless service checks (HTTP/HTTPS/TCP) ✅
- reachability-monitor: TCP/22 device liveness + status transitions ✅
- Dashboard: infrastructure health, empty states, live WebSocket, service-check widget ✅
- Onboarding wizard: 4 steps, integrations selection ✅
- HTTPS enforced: nginx redirects HTTP :3000 → HTTPS :3443 (self-signed bootstrap) ✅
- Backend tests: 514 passing (services/api) ✅

## In Progress
- Settings page redesign with sub-navigation

## Technology Stack
- Backend: Python 3.13, Django 6.0, DRF, Django Channels
- Frontend: React, TypeScript (.tsx), Vite, Tailwind CSS
- Charts: Apache ECharts
- Topology: Cytoscape.js
- Flow viz: D3.js
- State: Zustand
- Data fetching: React Query
- Auth: JWT (djangorestframework-simplejwt)
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
- checks: ServiceCheck, CheckResult (agentless synthetic monitoring)

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
/checks          → service checks (agentless synthetic monitoring)
/cve             → CVE exposure
/lifecycle       → EOL management
/settings/*      → settings sub-pages (see above)

## API Endpoints
/api/health/                    — platform health
/api/health/infrastructure/     — service connectivity check
/api/auth/token/                — JWT obtain
/api/auth/token/refresh/        — JWT refresh
/api/devices/                   — device CRUD (sortable: ?ordering=, filter: ?site=&status=)
/api/devices/topology/          — LLDP topology nodes + edges
/api/devices/test-connection/   — test device connectivity
/api/devices/:id/metrics/       — InfluxDB snapshot + series + lldp_neighbors + environment
/api/devices/:id/poll-now/      — trigger an immediate SNMP poll
/api/devices/:id/interfaces/    — monitored interfaces (GET/POST replace selection)
/api/devices/:id/interfaces/discover/ — SNMP/SSH interface + LLDP discovery
/api/devices/:id/topology/discover/   — LLDP neighbour discovery → TopologyLink
/api/credentials/               — credential profile CRUD
/api/credentials/:id/test/      — test credential against IP
/api/sites/                     — site CRUD (+ /:id/devices/)
/api/alerts/                    — alert rules/events/channels
/api/logs/                      — OpenSearch log query (filters incl. from/to on @timestamp)
/api/checks/                    — service check CRUD
/api/checks/:id/run-now/        — probe a check immediately
/api/checks/:id/results/        — check result history (?period=1h|6h|24h|7d)
/api/checks/summary/            — up/down/degraded/unknown counts
/api/cve/                       — CVE data
/api/lifecycle/                 — EOL data
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
python manage.py run_scheduler
python manage.py run_check_engine          # service checks (HTTP/HTTPS/TCP)
python manage.py run_reachability_monitor  # TCP/22 device liveness + status
python manage.py reset_test_data           # dev: clear app data, keep auth users

## Docker Compose Services
Infrastructure: postgres, influxdb, opensearch, valkey, nats, openbao
Application: api (port 8000), websocket (port 8001)
Frontend: frontend/nginx (port 3000)
Ingest: ingest-grpc, ingest-snmp, ingest-syslog, ingest-flow,
        ingest-otlp, ingest-api-poller
Engines: stream-processor, config-manager, alert-engine,
         cve-engine, lifecycle-engine, security-engine, scheduler,
         check-engine (service checks), reachability-monitor (TCP/22 liveness)

All engines build from ./services/api and share one image — use
`./netpulse.sh rebuild-api` to rebuild + recreate them all (see Development Workflow).

## Planned Features (NOT yet implemented)

The following are designed but not built — no models, endpoints, or services
exist for them yet. Do not document them as current.

- BGP looking glass: passive BGP route collector (e.g. ExaBGP), read-only.
  Planned models BGPSession/BGPPrefix, service bgp-monitor, endpoints /api/bgp/,
  /api/bgp/sessions/; frontend /bgp.
- Endpoint discovery: MAC address-table + ARP-table ingestion with OUI vendor
  lookup and IP/MAC search. Planned models MACEntry/ARPEntry, endpoint
  /api/endpoints/; frontend /endpoints.
- Service checks beyond Stage 1: ICMP/DNS/TLS/SMTP/SSH handlers (the
  ServiceCheck model already defines these check_types; only HTTP/HTTPS/TCP
  handlers are implemented in the runner today).

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

## Security Rules (NEVER violate)
1. Never store plaintext credentials anywhere
2. Always use OpenBao vault_path reference in PostgreSQL
3. Never return credential values in API responses
4. Always show "🔒 Stored securely in OpenBao" in credential UI
5. Scrub credentials from all logs
6. mTLS for all internal service communication
7. TLS 1.3 minimum for external connections
8. Zero secrets in code or environment variables in production

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

## PINNED — gNMI/SNMP Adaptive Polling (NOT yet built)

When a device is actively pushing gNMI telemetry, SNMP polling
for the same metrics is redundant and wastes device resources.

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
### Do NOT build until requested.

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
