# Metrics & Alerting Reference

This is the authoritative reference for the time-series **measurements**, their
**fields and tags**, and the alerting they drive. The schema below is what the
code actually writes (`apps/telemetry/.../run_stream_processor.py`,
`run_reachability_monitor.py`, `apps/telemetry/environment_poll.py`) and reads
(`apps/devices/metrics_influx.py`) — use these exact names in Flux queries.

Time-series live in **InfluxDB** (bucket `metrics` by default, `INFLUXDB_BUCKET`).
**Flow records are the exception** — they're written to **OpenSearch**
(`netpulse-flows-YYYY.MM`), not InfluxDB. Polling cadence follows the device's
`TelemetryConfig.snmp_interval_s` (default 300s) and the dedicated loops noted
per measurement.

---

## Available Metrics

### Device metrics — measurement `telemetry`

Per-device scalars, written each poll. **Tags:** `device_id`, `protocol`.

| Field | Type | Description |
|-------|------|-------------|
| `cpu_pct` | float | CPU utilization % (averaged across cores) |
| `memory_used_pct` | float | Memory utilization % |
| `memory_used_bytes` | int | Memory used (bytes) |
| `memory_total_bytes` | int | Total memory (bytes) |
| `temp_max_c` | float | Hottest temperature sensor (°C) |
| `fan_count` | int | Fans present |
| `psu_count` | int | PSUs present |

Additional raw GET metrics (uptime, platform-specific gauges) are merged in as
available. Alert examples: `cpu_pct > 90` sustained · `memory_used_pct > 85`.

### Interface metrics — measurement `interface_stats`

Per-interface **rates** (derived from counter deltas), written each poll.
**Tags:** `device_id`, `if_index` (gNMI also adds `if_name`). The reader resolves
`if_index` → interface name via `MonitoredInterface`.

| Field | Type | Description |
|-------|------|-------------|
| `in_bps` / `out_bps` | float | Throughput, **bits/s** (÷1e6 for Mbps) |
| `in_pps` / `out_pps` | float | Packets/s |
| `in_errors_rate` / `out_errors_rate` | float | Errors/s |
| `in_discards_rate` / `out_discards_rate` | float | Discards/s |
| `in_util_pct` / `out_util_pct` | float | % of link speed |
| `oper_status` | int | Operational status (1=up; reader maps to up/down) |

> The draft `interface.rx_mbps`/`tx_mbps` do **not** exist — use
> `interface_stats.in_bps`/`out_bps` (bits/s). Alert example: `in_util_pct > 80`.

### Environment metrics — measurement `device_environment`

Per-sensor, split by the `sensor_type` tag. Written by the SNMP path **and** the
dedicated REST poll (`environment_poll`, every `ENVIRONMENT_POLL_INTERVAL_S`=5m,
AOS-CX). **Common tags:** `device_id`, `sensor_name`, `sensor_type`.

**Temperature** (`sensor_type="temperature"`):

| Field | Type | Description |
|-------|------|-------------|
| `temperature_c` | float | Temperature (°C) |
| `status_ok` | int | 1 = healthy, 0 = fault |

**Fan** (`sensor_type="fan"`):

| Field | Type | Description |
|-------|------|-------------|
| `fan_rpm` | float | Fan speed (RPM; -1 = unknown) |
| `status` | string | `ok` / `fault` |

**PSU** (`sensor_type="psu"`):

| Field | Type | Description |
|-------|------|-------------|
| `watts` | float | Instantaneous power (W; -1 = unknown) |
| `status` | string | `online` / `offline` |

**PoE summary** (`sensor_type="poe"`, `sensor_name="poe"`):

| Field | Type | Description |
|-------|------|-------------|
| `poe_budget_watts` | float | Total PoE budget (W) |
| `poe_used_watts` | float | Current PoE used (W) |
| `poe_used_pct` | float | PoE utilization % |
| `poe_status` | string | `delivering` / `idle` / `unknown` |

> Use `temperature_c`/`status_ok` (not `value`/`warn_threshold`) and the
> `sensor_name`/`sensor_type` tags (not `sensor`). Temperature thresholds are
> static (`TEMP_WARNING_C`=75, `TEMP_CRITICAL_C`=85), not stored per-sensor.

### Reachability — measurement `device_reachability`

Written by the reachability monitor (~60s loop). **Tags:** `device_id`,
`hostname`.

| Field | Type | Description |
|-------|------|-------------|
| `is_reachable` | int | 1 = reachable, 0 = down |
| `rtt_ms` | float | Round-trip latency (only when reachable) |

Latency classification: warn `> PING_LATENCY_WARN_MS` (100), crit
`> PING_LATENCY_CRIT_MS` (500). (There is no `packet_loss_pct` field.)

### Transit latency — measurement `transit_latency`

Flow-derived inter-device latency. **Tags:** `src_device`, `dst_device`,
`ip_protocol`. **Field:** `latency_ms`.

### Flow records — OpenSearch `netpulse-flows-YYYY.MM` (not InfluxDB)

NetFlow v5/v9/IPFIX (UDP 2055) + sFlow (UDP 6343), via ingest-flow → NATS →
stream-processor. Each document: `exporter_ip`, `protocol_version`, `@timestamp`,
`src_ip`, `dst_ip`, `src_port`, `dst_port`, `ip_protocol`, `bytes`, `packets`,
`duration_ms`, `input_if`, `output_if`, `tcp_flags`, `tos`. Query via
`/api/flows/*` (top-talkers, summary, sankey, search). The
`flow-threshold-exceeded` alert fires from the stream-processor when an exporter
exceeds `ANOMALY_FLOW_THRESHOLD_MBPS` (1000).

### WAN circuit utilization — derived (not a stored measurement)

`GET /api/circuits/{id}/utilization/` maps the bound interface name → its
`interface_stats` `if_index` and computes, vs the circuit's configured bandwidth:
`rx_mbps`/`tx_mbps` (from `in_bps`/`out_bps`), `rx_pct`/`tx_pct`, plus 24h history,
peak, and the **95th percentile** (nearest-rank — the WAN-billing metric). Alert:
`rx_pct`/`tx_pct` > the circuit's `alert_threshold_pct`.

---

## Built-in alert rules (seeded)

Seeded by `seed_alert_rules` (idempotent) or auto-created by the engines. Each is
an `AlertRule` row you can disable in the UI (Settings → Alerting) by toggling
`is_active`. Thresholds are **not** in the rule — they come from env vars or
per-object settings (below).

| Rule name | Source / metric | Threshold (env/setting) | Severity |
|-----------|-----------------|-------------------------|----------|
| Device Unreachable | reachability `is_reachable` | down | critical |
| High Ping Latency | reachability `rtt_ms` | `PING_LATENCY_WARN_MS` 100 | warning |
| Ping Latency Critical | reachability `rtt_ms` | `PING_LATENCY_CRIT_MS` 500 | critical |
| Interface state change | `interface_stats.oper_status` | changed | warning |
| High Temperature Warning | `device_environment` temp | `TEMP_WARNING_C` 75 | medium |
| High Temperature Critical | `device_environment` temp | `TEMP_CRITICAL_C` 85 | critical |
| Temperature Sensor Failed | `status_ok` = 0 | n/a | high |
| flow-threshold-exceeded | flow Mbps | `ANOMALY_FLOW_THRESHOLD_MBPS` 1000 | high |
| High PoE Usage | `poe_used_pct` | `POE_ALERT_THRESHOLD_PCT` 80 | medium |
| High WAN Utilization | circuit util % | per-circuit `alert_threshold_pct` 80 | medium |
| WAN Contract Expiring | `contract_end_date` | 90/60/30/14/7 days | medium |
| Config Changed | config diff | any change | medium |
| Startup config not saved | running ≠ startup | mismatch | medium |

---

## Creating / customizing alert rules

spane does **not** use a PromQL-style `expr`. An `AlertRule` is a row with:
`name`, `description`, `severity`, `condition` (JSON describing the source/metric),
`channels`, `is_active`, `cooldown_minutes`. The engines (stream-processor,
reachability, check-engine, scheduler) evaluate the conditions and fire
`AlertEvent`s.

**Via UI:** Settings → Alerting → toggle/seed rules; route to channels under
Settings → Alerting (teams/policies/routes).

**Via API:** `POST /api/alerts/rules/`

```json
{
  "name": "High CPU Usage",
  "description": "CPU above threshold",
  "severity": "high",
  "condition": {"source": "stream-processor", "metric": "cpu_pct", "gt": 90},
  "is_active": true,
  "cooldown_minutes": 30
}
```

**Tuning thresholds** (no redeploy needed for env vars — set in `.env`, then
`./netpulse.sh rebuild-api`): `ANOMALY_FLOW_THRESHOLD_MBPS`, `POE_ALERT_THRESHOLD_PCT`,
`TEMP_WARNING_C` / `TEMP_CRITICAL_C`, `PING_LATENCY_WARN_MS` / `PING_LATENCY_CRIT_MS`,
and per-circuit `alert_threshold_pct` in the circuit edit modal.

---

## Querying metrics directly (Flux)

```flux
// CPU over 80% in the last hour, by device
from(bucket: "metrics")
  |> range(start: -1h)
  |> filter(fn: (r) => r._measurement == "telemetry" and r._field == "cpu_pct" and r._value > 80)
  |> group(columns: ["device_id"])

// Interface throughput (Mbps) for one interface over 24h
from(bucket: "metrics")
  |> range(start: -24h)
  |> filter(fn: (r) => r._measurement == "interface_stats" and r.device_id == "42" and r.if_index == "10")
  |> filter(fn: (r) => r._field == "in_bps" or r._field == "out_bps")
  |> aggregateWindow(every: 5m, fn: mean)
  |> map(fn: (r) => ({ r with _value: r._value / 1000000.0 }))   // bps → Mbps

// PoE utilization across all switches (latest)
from(bucket: "metrics")
  |> range(start: -1h)
  |> filter(fn: (r) => r._measurement == "device_environment" and r.sensor_type == "poe" and r._field == "poe_used_pct")
  |> last()

// Temperature sensors above 50 °C (latest)
from(bucket: "metrics")
  |> range(start: -1h)
  |> filter(fn: (r) => r._measurement == "device_environment" and r.sensor_type == "temperature" and r._field == "temperature_c")
  |> filter(fn: (r) => r._value > 50.0)
  |> last()
```

---

## Collection status

```bash
# Per-device collection method + freshness
#   UI: a device's Telemetry tab → collection badges
#   API: GET /api/devices/{id}/collection-status/

# Stack health
./netpulse.sh status

# List InfluxDB measurements (inside the api container's network)
docker compose exec influxdb influx query \
  'import "influxdata/influxdb/schema" schema.measurements(bucket: "metrics")'
```

---

## Retention

Retention is governed by the **InfluxDB bucket** (`metrics`) and the OpenSearch
ISM policy for flow/log indices — set at deploy time, not yet a runtime UI
control. Recommended targets when sizing storage:

| Data | Store | Suggested retention |
|------|-------|---------------------|
| `telemetry` / `interface_stats` | InfluxDB | 90 days |
| `device_environment` | InfluxDB | 365 days (trending) |
| `device_reachability` | InfluxDB | 30–90 days |
| Flow records | OpenSearch | 30 days (high volume) |

Alert-event history retention (PostgreSQL) is the separate
`ALERT_RETENTION_DAYS` (90), pruned daily by the scheduler.

---

See also: `docs/flow-collection.md` (flow ingest), `docs/platforms/aos_cx.md`
(environment/PoE collection), and the **Metrics & Alerting** notes in
`CLAUDE.md`.
