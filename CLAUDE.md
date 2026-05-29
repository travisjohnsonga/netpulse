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
| `./backend` | `api`, `websocket`, `stream-processor`, `config-manager`, `alert-engine`, `cve-engine`, `lifecycle-engine`, `security-engine`, `scheduler` |
| `./frontend` | `frontend` |
| `./ingest` | `ingest-grpc`, `ingest-snmp`, `ingest-syslog`, `ingest-flow`, `ingest-otlp` |

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
