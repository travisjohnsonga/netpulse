# NetPulse

**Push-first, open source network intelligence platform.**

## Quick Install

\`\`\`bash
curl -fsSL https://raw.githubusercontent.com/travisjohnsonga/netpulse/main/scripts/install.sh | bash
\`\`\`

## Features

- Push-first telemetry (gRPC/gNMI, SNMP, NetFlow/sFlow)
- CVE intelligence with config-aware applicability
- Configuration compliance engine
- Flow analytics with Sankey diagrams
- Multi-vendor support (Cisco, Fortinet, HPE, SonicWall, Ubiquiti UniFi)
- Discovery with import preview, periodic hostname verification, and per-site credential assignment
- Integrations: NetBox import (with preview), UniFi (multi-controller + cloud auto-discovery), Email/SMTP alerts

## Documentation

- [Architecture](ARCHITECTURE.md)
- [Deployment Guide](setup/deployment.md)
- [Platform Support](platforms/sonicwall.md)
- Integrations: [UniFi](integrations/unifi.md) · [NetBox](integrations/netbox.md) · [Email/SMTP](integrations/email.md)
