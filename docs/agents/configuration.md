# Agent Configuration

The agent reads a JSON config file written during enrollment.

| OS | Path |
|----|------|
| Linux   | `/etc/netpulse-agent/config.json` |
| Windows | `C:\ProgramData\NetPulse\config.json` |

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
