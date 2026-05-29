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
- gRPC/gNMI: port 50051 (structured telemetry from network devices)
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
  -p 50051:50051 \
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
