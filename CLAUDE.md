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
