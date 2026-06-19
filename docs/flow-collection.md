# Flow Collection

spane's `ingest-flow` service collects network flow data on:

| Port | Protocol | Formats |
|---|---|---|
| **UDP 2055** | NetFlow / IPFIX | NetFlow v5, v9, IPFIX (v10) |
| **UDP 6343** | sFlow | sFlow v4 / v5 |

Both ports are exposed by the `ingest-flow` container (`docker-compose.yml`);
the host ports are configurable via `NETFLOW_PORT` / `SFLOW_PORT` in `.env`
(defaults 2055 / 6343). Configure your devices to export flow to your spane
server's IP on these ports.

## Device configuration

### AOS-CX (NetFlow / IPFIX)

```
ip flow-export destination {spane_ip} 2055
ip flow-export version 9
```

### Cisco IOS (NetFlow)

```
ip flow-export destination {spane_ip} 2055
ip flow-export version 9
interface GigabitEthernet0/0
  ip flow ingress
  ip flow egress
```

### sFlow

Point the switch's sFlow collector at `{spane_ip}` UDP **6343**.

## Notes

- **IPFIX (v10) is preferred** where the platform supports it — it carries more
  fields and offers better template flexibility than NetFlow v9.
- Flow records are published to NATS by `ingest-flow` and fanned out by the
  stream-processor to InfluxDB/OpenSearch (see `docs/ARCHITECTURE.md`).
- If flows don't appear, confirm the export destination IP/port, that the host
  firewall allows the UDP port, and that the device is actually sampling
  traffic (sFlow) or has flow caches active (NetFlow).
