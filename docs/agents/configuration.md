# Agent Configuration

The agent reads a JSON config file written during enrollment.

| OS | Path |
|----|------|
| Linux   | `/etc/netpulse-agent/config.json` |
| Windows | `C:\ProgramData\spane\config.json` |

## Reference

```json
{
  "server_url": "https://netpulse.company.com",
  "agent_id": "uuid",
  "cert_path": "/etc/netpulse-agent/agent.crt",
  "key_path": "/etc/netpulse-agent/agent.key",
  "ca_cert_path": "/etc/netpulse-agent/ca.crt",
  "insecure_tls": false,

  "collection": {
    "interval": 30,
    "cpu": true,
    "memory": true,
    "disk": true,
    "network": true,
    "processes": false,
    "services": false
  },

  "role_checks": {
    "enabled": false,
    "roles": ["dns", "web"],
    "extra_services": {
      "linux": ["myapp.service"],
      "windows": ["MyWindowsService"]
    }
  },

  "log": { "level": "info", "output": "" }
}
```

- `insecure_tls` — skip server-cert verification (set during enrollment from
  `--insecure` or an `http://` server URL). mTLS is preferred; leave `false` in
  production.
- `collection.interval` — seconds between pushes. Default 30; minimum 10;
  30–60 recommended.
- `collection.services` — when `true`, the agent reports running service names
  with each push, enabling **role auto-detection** on the server.

## Role checks

Set `role_checks.enabled: true` and list `roles`. The agent runs the role's
service/port checks each interval and reports results; the server records the
assignment and shows pass/fail on the server's **Roles** tab. `extra_services`
adds host-specific units to watch on top of the role's built-ins.

Built-in roles:

| Role     | Type              | Monitors                                  |
|----------|-------------------|-------------------------------------------|
| `dhcp`   | DHCP Server       | DHCPServer / dhcpd, port 67               |
| `dns`    | DNS Server        | named / DNS, port 53                      |
| `nps`    | NPS / RADIUS      | IAS / freeradius, ports 1812–1813         |
| `dc`     | Domain Controller | NTDS / ADWS / Netlogon                    |
| `web`    | Web Server        | IIS / nginx / apache, ports 80/443        |
| `db`     | Database Server   | MSSQL / postgres / mysql                  |
| `file`   | File Server       | LanmanServer, port 445                    |
| `syslog` | Syslog Server     | rsyslog / syslog-ng, port 514             |

Roles can also be assigned from the UI (Servers → server → **Roles** tab),
either manually or from auto-detection of running services. Manual and
config-declared assignments converge on the same list.

After editing the config, restart the service (`systemctl restart
netpulse-agent`).

## Server-managed configuration (pull)

Most settings are managed from the **server**, not by hand-editing the file. The
agent's metrics-push **response** carries a `desired_config` (with a
`desired_version`); the agent applies it on its next ~30s check-in. Operators set
it from the UI (Servers → server → **Config**) or `PATCH /api/servers/{id}/config/`
(audit-logged, `AGENT_CONFIG_CHANGED`); the server validates it against an
allowlist (`AgentConfigSerializer`).

```json
{
  "collection": { "interval": 30, "cpu": true, "memory": true,
                  "disk": true, "network": true, "processes": false, "services": false },
  "logs": { "enabled": false, "sources": ["auth", "service", "kernel"], "additional_paths": [] },
  "stability": { "services": ["sshd", "docker"] },
  "functional": { "web": { "urls": [] } }
}
```

- **`collection.*`** — the same collection toggles as the local config, but
  server-managed (pull-applied).
- **`logs`** — **Stage 1 log forwarding.** When `enabled`, the agent tails the
  curated security sources (`auth`/`service`/`kernel`) plus any allowlisted
  `additional_paths` and ships raw lines over mTLS → NATS → OpenSearch. ⚠️ Stage 1
  is currently under-flowing (see Known issues).
- **`stability.services`** — **role-independent** watched services. The agent
  reports each one's running state + restart history; the server fires/auto-resolves
  **"Service Down"** and **"Service Flapping"** alerts. Watch the services you care
  about regardless of role.
- **`functional.web`** — the **web-role functional health check.** `urls` is an
  optional override; left empty, the agent derives `http://localhost:80/` +
  `https://localhost:443/` from the role's ports. Each URL is probed for
  HTTP health (2xx/3xx healthy → 4xx warning → 5xx degraded → error down) and TLS
  cert expiry; the server fires site-down/site-degraded/cert-expiring(≤30d) alerts.
  **SSRF guard:** URLs must be `http`/`https` to a loopback host (enforced both
  server-side in the serializer and agent-side in Go, including redirect hops).
