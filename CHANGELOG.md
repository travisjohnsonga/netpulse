# Changelog

All notable changes to spane (NetPulse) are documented here. Versions follow the
`1.0.<commit-count>` scheme reported by `./netpulse.sh show-version` and
`GET /api/health/`.

## Unreleased

### Added
- **Alert dispatch layer + email/Teams notifiers** — AlertEvents now deliver to
  the configured `AlertChannel`s (previously fired/showed in the UI but sent
  nothing). A single `apps/alerts/dispatch.py` choke point is wired via an
  AlertEvent `post_save` signal (+ the `.update()`-based resolve paths), so every
  alert source (interface/reachability/stability/liveness/functional/environment/
  circuits/compliance/…) routes through the same dispatch. Pluggable notifier
  registry: **email** (HTML + text, recipients from channel config) and
  **Microsoft Teams** (Adaptive Card or legacy MessageCard, severity-coloured,
  "View in spane" button, green recovery card on resolve), plus webhook/slack/
  PagerDuty notifiers. Fire/resolve **debounce** (notify once per FIRING + once
  per RESOLVED via `fired_notified_at`/`resolved_notified_at`, atomic claim — a
  flapping alert can't spam), per-channel **retry/backoff**, and **failure
  isolation** (one bad channel never blocks the others or crashes the engine).
  Severity-threshold + label-routing channel matching; `config.all_alerts` global
  channels. Channel secrets (Teams/webhook URLs, PagerDuty routing keys) stored in
  OpenBao. New `TEAMS` channel type (alerts migration 0004). `POST /api/alerts/
  channels/{id}/test/` and `manage.py fire_test_alert` for verification.
  `tests/test_alert_dispatch.py` (33). Disabled in the test suite via
  `ALERT_DISPATCH_ENABLED`.
- **Service stability monitoring** (role-independent): operator-watched services
  (`desired_config.stability.services`), `WatchedServiceStatus`, **Service Down**
  + **Service Flapping** alerts (debounce + auto-resolve).
- **Agent liveness alerting**: an **Agent Offline** alert when an agent stops
  reporting past its threshold (`run_scheduler` `agent_liveness`, 60s; per-agent
  suppress for lab boxes).
- **Web-role functional health check** (agent v1.5.0): HTTP/cert probe with any-of
  health resolution; the web-role card headline leads with the functional verdict
  ("✓ Healthy · cert Nd") instead of a service-count. Loopback-only SSRF allowlist.
- **Agent log forwarding — Stage 1**: agent tails curated security logs → mTLS →
  NATS → OpenSearch. *(Under-flowing; open diagnosis.)*
- **Agent OS detail + rich service detail**; ServerDetail Services-tab table.
- **Shared `useTabParam` hook**: tab-in-URL persistence across ServerDetail,
  DeviceDetail, SiteDetail, Settings, and the Sites view toggle.
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
- **Stream-processor log/flow durability**: logs/flows are now acked only after a
  successful OpenSearch write (was acked-on-receipt → silent loss on an OpenSearch
  blip); NAK→redeliver on failure, with a poison-message drop after
  `STREAM_PROCESSOR_MAX_DELIVER` (5) attempts so a doc OpenSearch always rejects
  can't wedge the consumer. Transient outages never drop (durable in the stream).
- **Agent device records no longer pollute the network views**: agent-backed
  servers (synthetic/loopback Device records) are excluded from the **Devices
  list** and the **reachability monitor** (they belong on the Servers page and
  report their own liveness); resolved the resulting stale false
  `device-unreachable` alerts.
- **Devices list**: the row Connect button moved to a right-aligned plain-text
  **SSH** action column.
- **CI**: `agent-latest` rolling release is republished only on tag pushes, so
  main-branch pushes no longer clobber it.
- flow-threshold alert: exporter IP was truncated to the first octet; throughput
  units (a 0-duration record read as 100s of Gbps); + device/top-talker context.
- Compliance score on the device list showed template-only instead of the
  weighted combined score.

### Changed
- **Text-only left-nav sidebar** — emoji nav icons removed (cleaner enterprise
  tone); badges/collapse/active-state/capability gating retained.
- **Alerts page** defaults to **actionable-only** (firing + unacknowledged) with a
  "show resolved & acknowledged" toggle.

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
