# NetPulse

**Push-first, open source network intelligence platform.**

## Quick Install

\`\`\`bash
curl -fsSL https://raw.githubusercontent.com/travisjohnsonga/netpulse/main/scripts/install.sh | bash
\`\`\`

## Features

- Push-first telemetry (gRPC/gNMI, SNMP, NetFlow/sFlow)
- Secure agent-based server monitoring (Linux + Windows; mTLS, OpenBao PKI) with
  a dedicated Servers page
- Role-based server monitoring (DNS, DHCP, NPS, Domain Controller, Web, DB, File,
  Syslog) — manual + auto-detected
- CVE intelligence with config-aware applicability
- Configuration compliance engine
- Flow analytics with Sankey diagrams
- Multi-vendor support (Cisco, Fortinet, HPE, SonicWall, Ubiquiti UniFi)
- LLDP neighbor discovery (persisted topology, undiscovered-neighbor view)
- Discovery with import preview, periodic hostname verification, and per-site credential assignment
- Audit log (40+ event types, CSV export)
- Integrations: NetBox import (with preview), UniFi (multi-controller + cloud auto-discovery), Email/SMTP alerts

## Documentation

- [Architecture](ARCHITECTURE.md)
- [Deployment Guide](setup/deployment.md)
- Agent: [Overview](agents/overview.md) · [Installation](agents/installation.md) · [Configuration](agents/configuration.md) · [Security](agents/security.md)
- [Platform Support](platforms/sonicwall.md)
- Integrations: [UniFi](integrations/unifi.md) · [NetBox](integrations/netbox.md) · [Email/SMTP](integrations/email.md)
