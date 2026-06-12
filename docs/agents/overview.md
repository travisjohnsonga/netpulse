# spane Agent

Lightweight, secure monitoring agent for Linux and Windows servers. The agent
enrolls into spane with a one-time token, receives a unique client
certificate from spane's internal CA, and pushes CPU / memory / disk /
network metrics and role-check results over mutual TLS.

## Features

- CPU, memory, disk, and network monitoring (per-core, per-mount, per-interface)
- Optional running-process and service collection
- Role-based service monitoring (DNS, DHCP, NPS, Domain Controller, Web, DB,
  File, Syslog) — see [Configuration](configuration.md)
- Secure mTLS communication; **no inbound ports**
- Auto-enrolls the server into the spane inventory (and the Servers page)
- Single static binary (the Linux core build is stdlib-only)

## Architecture

```
 Agent  ──  mTLS, outbound 443  ──▶  spane (nginx → api)
```

The agent:

- Runs as a low-privilege system service (systemd / Windows Service)
- Opens **no** inbound ports — all traffic is outbound HTTPS to the server
- Authenticates with a client certificate issued by spane's CA (OpenBao PKI)
- nginx terminates the mTLS connection, verifies the client cert against the
  agent CA, and forwards the verified cert serial to Django, which matches it to
  the enrolled agent

Metrics land in the same InfluxDB measurements (`cpu`/`memory`/`disk`/`interface`)
as SNMP/REST collection, so agent-monitored servers appear on the
**Servers** page with full drill-down.

## Supported platforms

| Platform | Architecture | Status        |
|----------|--------------|---------------|
| Linux    | amd64        | ✅ Supported  |
| Linux    | arm64        | ✅ Supported  |
| Windows  | amd64        | ✅ Supported  |

Binaries are built by CI. See [Installation](installation.md) to get started and
[Security](security.md) for the trust model.
