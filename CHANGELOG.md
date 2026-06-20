# Changelog

All notable changes to spane (NetPulse) are documented here. Versions follow the
`1.0.<commit-count>` scheme reported by `./netpulse.sh show-version` and
`GET /api/health/`.

## Unreleased

- Safe `./netpulse.sh update` (snapshot rollback point, `.env` back-fill, DB
  backup, explicit migrations, ordered rebuild, post-update health verify) +
  `show-version` / `rollback` subcommands.
- Per-user temperature unit preference (°C/°F) across device telemetry.
- Periodic AOS-CX environment + PoE collection → InfluxDB, with a standing
  "High PoE Usage" alert.
- Daily scheduled fleet compliance run; run-all / single-device compliance run
  endpoints + UI triggers; unified weighted compliance score on the device list.
- Alerts page bulk acknowledge/resolve + state filter tabs.
- Flow Analytics DNS hostname enrichment.
- Exception-exposure (CWE-209) regression guard: `tests/test_security.py`,
  `scripts/check_exception_exposure.py`, pre-commit hook + CI.
- Security fixes: form-data CRLF + js-yaml DoS dependency bumps.
- flow-threshold alert correctness (full exporter IP, sane Mbps, device context).

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
