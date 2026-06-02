# NetPulse — Network Intelligence Platform

![License](https://img.shields.io/badge/license-Apache%202.0-blue)
![Tests](https://img.shields.io/badge/tests-759%20passing-green)
![Docker](https://img.shields.io/badge/docker-24%20services-blue)

> Push-first, open source network intelligence platform.
> Built for modern infrastructure, vendor-agnostic,
> deployable on-prem via Docker Compose.

NetPulse handles gRPC/gNMI streaming telemetry, config compliance, CVE
intelligence, lifecycle management, log anomaly detection, and unified risk
scoring — all open source, all containerized.

---

## System Requirements

Size the host to your fleet.

### Supported Operating Systems
- Ubuntu 22.04+ (recommended: Ubuntu 24.04 LTS)
- RHEL 9+ / Alma Linux 9+ / Rocky Linux 9+
- Debian 12+

### Small Deployment (< 50 devices)
| Component | Minimum | Recommended |
|-----------|---------|-------------|
| CPU       | 4 cores | 4 cores     |
| RAM       | 8 GB    | 16 GB       |
| Disk      | 50 GB   | 100 GB SSD  |

### Medium Deployment (50-200 devices)
| Component | Minimum | Recommended |
|-----------|---------|-------------|
| CPU       | 8 cores | 16 cores    |
| RAM       | 16 GB   | 32 GB       |
| Disk      | 100 GB  | 500 GB SSD  |

### Large Deployment (200+ devices)
| Component | Minimum  | Recommended |
|-----------|----------|-------------|
| CPU       | 16 cores | 32 cores    |
| RAM       | 32 GB    | 64 GB       |
| Disk      | 500 GB   | 2 TB SSD    |
| Note      | Kubernetes deployment (Helm chart planned) ||

### Notes
- **SSD strongly recommended** for InfluxDB + OpenSearch
- Disk usage grows with number of devices, log volume, config retention,
  NetFlow/sFlow
- RAM dominated by OpenSearch (4 GB min) and InfluxDB (2 GB min)
- CPU spikes during discovery scans (nmap)
- gNMI streaming: add ~50 MB RAM per 100 devices

---

## Prerequisites

### Required Software

1. **Docker Engine** (v24.0+)

   Ubuntu/Debian:
   ```bash
   curl -fsSL https://get.docker.com | sh
   sudo usermod -aG docker $USER
   newgrp docker
   ```

   RHEL/Alma/Rocky:
   ```bash
   sudo dnf install -y docker-ce docker-ce-cli
   sudo systemctl enable --now docker
   sudo usermod -aG docker $USER
   ```

2. **Docker Compose** (v2.20+) — included with Docker Desktop.

   For Linux:
   ```bash
   sudo apt-get install docker-compose-plugin   # Debian/Ubuntu
   sudo dnf install docker-compose-plugin        # RHEL/Alma/Rocky
   ```

   Verify:
   ```bash
   docker compose version   # Docker Compose version v2.x.x
   ```

3. **Git**
   ```bash
   sudo apt-get install git    # Debian/Ubuntu
   sudo dnf install git         # RHEL/Alma/Rocky
   ```

### Optional but Recommended
- **nmap** (for network discovery on the host): `sudo apt-get install nmap`.
  Also installed inside the containers automatically.
- **Python 3.11+** — for helper scripts only; not required to run NetPulse.

### Network Requirements

Ports that must be accessible on the NetPulse server:

| Port  | Protocol | Service                    | Direction              |
|-------|----------|----------------------------|------------------------|
| 3443  | TCP      | HTTPS Web UI               | Inbound                |
| 3000  | TCP      | HTTP (redirects to HTTPS)  | Inbound                |
| 514   | UDP      | Syslog receiver            | Inbound from devices   |
| 57400 | TCP      | gNMI/MDT streaming         | Inbound from devices   |
| 2055  | UDP      | NetFlow/sFlow              | Inbound from devices   |
| 161   | UDP      | SNMP (outbound polls)      | Outbound to devices    |
| 22    | TCP      | SSH (config collection)    | Outbound to devices    |

### Device Requirements

For full telemetry support, network devices need:
- **SNMP v3** configured (recommended) or v2c
- **SSH** access for config collection
- **Syslog** forwarding to the NetPulse IP
- **gNMI/MDT** (optional, for streaming telemetry)
  - Cisco IOS-XE: MDT on port 57400
  - See **Settings → Telemetry Configuration** for device-specific
    configuration snippets

---

## Quick Start

### 1. Clone the repository
```bash
git clone https://github.com/travisjohnsonga/netpulse.git
cd netpulse
```

### 2. Run setup
```bash
./scripts/setup.sh
```
This generates secure random secrets, initialises credential storage (OpenBao),
creates the admin account, and configures all services.

> **Note:** the admin password must be 12+ characters.

### 3. Start NetPulse
```bash
docker compose up -d
```
First startup takes 2-3 minutes while services initialise. Monitor progress:
```bash
docker compose logs -f api
```
Wait for: `starting: gunicorn config.wsgi:application`.

### 4. Access the UI
```
https://YOUR_SERVER_IP:3443
```
Log in with the admin credentials set during setup.

> **Note:** the default install uses a self-signed certificate, so your browser
> shows a security warning — this is expected. Add a proper TLS certificate in
> **Settings → SSL** for production use.

### 5. Add your first device
1. Go to **Settings → Discovery**
2. Create a new job with your network subnet
3. Approve discovered devices
4. Or manually: **Devices → + Add Device**

### Secrets (OpenBao)

OpenBao runs with **persistent file storage** and is initialised + unsealed
automatically on first start — no manual `operator init`/`unseal` needed. The
`api` service writes the unseal key + root token to `/openbao/data/.init_keys`
(mode `600`) on the `openbao-data` volume and auto-unseals from it on later
starts, so device credentials, git tokens and feed API keys persist across
restarts.

> ⚠️ `/openbao/data/.init_keys` holds your unseal key and root token. It lives
> on the Docker volume and is git-ignored — never commit it, and back it up
> securely (losing it means you cannot unseal OpenBao after a restart).
> Leave `OPENBAO_TOKEN` blank in `.env` unless using an externally-managed token.

---

## Auto-start on Boot (Linux)

```bash
sudo tee /etc/systemd/system/netpulse.service << 'EOF'
[Unit]
Description=NetPulse Network Intelligence Platform
Requires=docker.service
After=docker.service network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
User=YOUR_USERNAME
WorkingDirectory=/path/to/netpulse
ExecStart=/usr/bin/docker compose up -d
ExecStop=/usr/bin/docker compose down
TimeoutStartSec=300

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable netpulse.service
```

---

## Upgrading

```bash
git pull origin main
docker compose build
docker compose up -d
docker compose exec api python manage.py migrate
```

---

## Factory Reset (Development Only)

> ⚠️ **DESTROYS ALL DATA — development use only.**

```bash
./scripts/factory-reset.sh
```

After reset, re-run setup:
```bash
./scripts/setup.sh
./netpulse.sh rebuild-api
```

---

## Architecture

24 Docker services including:
- Django 6.0 REST API
- React + TypeScript frontend
- InfluxDB (time-series metrics)
- PostgreSQL (primary database)
- OpenSearch (logs and flows)
- OpenBao (secrets management)
- NATS JetStream (message bus)
- Valkey (cache + WebSocket broker)

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for full details.

---

## Supported Platforms

| Platform          | SNMP | SSH | gNMI | Syslog |
|-------------------|------|-----|------|--------|
| Cisco IOS-XE      | ✅   | ✅  | ✅   | ✅     |
| Cisco IOS         | ✅   | ✅  | —    | ✅     |
| Cisco IOS-XR      | ✅   | ✅  | ✅   | ✅     |
| Cisco NX-OS       | ✅   | ✅  | ✅   | ✅     |
| Juniper JunOS     | ✅   | ✅  | ✅   | ✅     |
| Arista EOS        | ✅   | ✅  | ✅   | ✅     |
| Fortinet FortiOS  | ✅   | ✅  | —    | ✅     |
| Palo Alto PAN-OS  | ✅   | ✅  | —    | ✅     |

---

## License

Apache 2.0 — see [LICENSE](LICENSE) for details.

---

## Contributing

Community contributions welcome!
- SNMP MIB files
- TextFSM templates
- Vendor advisory YAML files
- Platform-specific telemetry paths
- Bug reports and feature requests

[Open an Issue](https://github.com/travisjohnsonga/netpulse/issues)
