# Changelog

All notable changes to spane (NetPulse) are documented here. Versions follow
**`app-vX.Y.Z` semver** — the app's own tag prefix (distinct from the agent's
`vX.Y.Z`), reported (stripped to `X.Y.Z`) by `./netpulse.sh show-version` and
`GET /api/health/`. Minor = features, patch = fixes; dev builds between tags
report `X.Y.Z-<n>-g<sha>`.

## 0.7.0 — 2026-06-30

The **alerting reliability + control** release: makes notification delivery
observable and correct, and adds the generation-vs-notification controls
(min-severity, per-rule, per-device silencing) on top of the 0.6.0 dispatch
substrate. Plus the per-server role-check correctness fix and UI polish. Shipped
as nine PRs (#151–#160) so each lands with its own review + changelog.

### Added
- **Delivery reliability** (#152) — every notification attempt is now recorded in
  a new **`NotificationLog`** (per-channel status, attempts, detail, timestamp),
  so delivery is observable instead of fire-and-forget. A **cross-channel
  meta-alarm** raises an alert when a channel fails and routes that warning
  through the **surviving** channels (a dead Teams webhook is reported via email,
  and vice-versa). New **delivery-health** endpoints, integrated into
  `GET /api/health/` (per-channel health summary). Per-channel **retry/backoff**
  and **failure isolation** (one bad channel never blocks the others).
- **Delivery-health UI** (#154) — a degraded-delivery **banner**, a
  **`/notifications`** delivery-log page, and a delivery-health row on
  **PlatformStatus**.
- **Notification control — generation-vs-notification split at every level**
  (#151, #155) — the `AlertEvent` is always generated (for the UI), but
  *notification* is gated independently: per-channel **`min_severity`**, per-type
  **UI-only** types (audit-style events like `config_changed` never page), and a
  per-rule **notify toggle** (observe-only rules).
- **Per-device/server silencing** (#156) — **`alerting_enabled`** (permanent
  observe-only, e.g. a dev/test box) and **`silenced_until`** (timed,
  auto-expiring mute) for **both** network devices and agent servers. Neither
  suppresses the `AlertEvent` record — only the notification.
- **Per-server role-check config** (#157, #159) — a **Custom functional-web mode**
  (specify exact on-host URLs/ports), per-server **service multi-select** (count
  only the services this host actually runs → kills the false `not_found` in a
  role's X/Y service-check count), and a **stability-link checkbox** (watch a role
  service for down/flap from the role card).

### Changed
- **Alert/log subject routing by `device_kind`** (#153) — alert and log subjects
  link to **`/servers/`** for agent servers and **`/devices/`** for network
  devices (previously always `/devices/`).
- **TV/NOC dashboard emoji cleanup** (#160) — TV dashboards use colored text
  status labels (UP/DOWN) and text affordances instead of emoji, for a clean
  NOC-wall aesthetic (matches the text-only sidebar direction).

### Fixed
- **"Ask spane" FAB overlapped the last list row** (#158) — added bottom padding
  to the global scroll container so the floating chat button no longer covers
  bottom-of-list content (e.g. the final Alert Rules row's Notify/Enabled
  toggles).
- **Custom web-check mode was unreachable** (#159) — the functional mode was
  derived purely from the URL list, so picking "Custom" snapped back to HTTP-only
  and the input never opened; an explicit mode-intent state now keeps the Custom
  input open regardless of URL values.
- **Stale Services/Roles tab after a config write** (#159) — the server-config tab
  now re-fetches the server on save, so watched-service and role-status changes
  reflect immediately without a hard refresh.

## 0.6.0 — 2026-06-28

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

## 0.1.0 — 2026-06-01

- Initial release.
- Push-first telemetry (gRPC/gNMI, SNMP fallback), NetFlow/sFlow/IPFIX flow
  analytics, syslog + OTLP ingest.
- Config compliance engine (templates, interface rules, role consistency,
  running/startup match) and regulatory framework reporting.
- CVE intelligence, OS lifecycle, unified device risk scoring.
- Device discovery, topology, ARP/MAC, LLDP.
- Alerting + routing, reporting subsystem, TV/NOC dashboards.
- spane Agent (server monitoring), platform backup & restore.
