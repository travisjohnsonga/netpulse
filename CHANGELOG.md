# Changelog

All notable changes to spane (NetPulse) are documented here. Versions follow the
`1.0.<commit-count>` scheme reported by `./netpulse.sh show-version` and
`GET /api/health/`.

## Unreleased

### Added
- **WAN circuit tracking** (`apps.circuits`): provider/bandwidth/contract details,
  ISP IP assignment (IPv4/IPv6 block, gateway, BGP ASNs), device+interface
  binding, per-circuit alert threshold. Utilization from InfluxDB interface
  metrics with **95th-percentile** trending; **High WAN Utilization** + **WAN
  Contract Expiring** (90/60/30/14/7 days) alerts. `/circuits` page + sidebar;
  Site-detail WAN Circuits tab populated.
- **Manual topology links** for LLDP/CDP-less devices: typed
  (ethernet/fiber/wan/lacp/mgmt/virtual/other), interfaces on both ends, speed,
  description; drawn as **dashed, colour-by-type** edges on the topology map;
  Settings → Network → Manual Links page; audit-logged.
- **Periodic environment/PoE collection** to InfluxDB (AOS-CX, every 5m) for
  alerting/trending + a **High PoE Usage** alert (>80% budget).
- **Compliance**: `DeviceComplianceScore` stores the weighted combined score so
  the device list matches the Compliance tab; `run_compliance_for_device` always
  persists it; on-demand **run-all** / single-device run endpoints + UI triggers;
  **daily scheduled run at 03:00** (after the 02:00 backup).
- **Alerts**: bulk acknowledge/resolve (`/api/alerts/events/bulk-{acknowledge,
  resolve}/`), checkbox selection + select-all, 5+ confirmation, and
  All/Firing/Acknowledged/Resolved filter tabs with counts.
- **Flow Analytics DNS enrichment**: inventory-first + reverse-DNS hostname
  resolution (`/api/flows/resolve/`, cached 5m, parallel, ≤100 IPs/req), toggle
  persisted to localStorage + `?resolve=1`.
- **User temperature unit preference** (°C/°F) — Profile → Display; applied across
  the device Environment tab, Wireless and Console telemetry.
- **Safe update**: `./netpulse.sh update` (snapshot tag, `.env` back-fill, DB
  backup, explicit migrate, ordered rebuild, health verify, `.update-history.log`)
  + `show-version` / `rollback`.
- **Version tracking**: root `VERSION` file + `SPANE_VERSION` env; `/api/health/`
  reports the real version (was "unknown"); shown in Settings → System + TV footer.

### Fixed
- flow-threshold alert: exporter IP was truncated to the first octet; throughput
  units (a 0-duration record read as 100s of Gbps); + device/top-talker context.
- Compliance score on the device list showed template-only instead of the
  weighted combined score.

### Security
- Dependabot: **form-data** CRLF (#8) → 4.0.6, **js-yaml** DoS (#7) → 4.2.0
  (DOMPurify #5/#6 in the prior sweep).
- Exception-exposure (CWE-209) scrubbed across views; enforced by
  `tests/test_security.py`, `scripts/check_exception_exposure.py`, a pre-commit
  hook and a CI workflow.

## v0.1.0 — 2026-06-01

- Initial release.
- Push-first telemetry (gRPC/gNMI, SNMP fallback), NetFlow/sFlow/IPFIX flow
  analytics, syslog + OTLP ingest.
- Config compliance engine (templates, interface rules, role consistency,
  running/startup match) and regulatory framework reporting.
- CVE intelligence, OS lifecycle, unified device risk scoring.
- Device discovery, topology, ARP/MAC, LLDP.
- Alerting + routing, reporting subsystem, TV/NOC dashboards.
- spane Agent (server monitoring), platform backup & restore.
