# Reports

spane generates two operational reports on demand or on a schedule, in
**PDF / CSV / JSON / HTML**. The **Reports** page (`/reports`) drives generation,
preview, scheduling, and download; artifacts are kept under
`MEDIA_ROOT/reports/{year}/{month}/` and served only through the authenticated
`/api/reports/{id}/download/` endpoint.

## Compliance Summary

Fleet compliance, grouped by **site**, **role**, and **platform**, using the
weighted device score (see [compliance/overview](compliance/overview.md)):

- Fleet summary: total / passing / warning / failing / not-checked + average score.
- Per-group rows: device count, average score, grade, pass/fail, top issues.
- Findings by severity (failing = critical, warning) and the **startup-config
  mismatch** list (reboot risk).

A shared role-consistency cache means each rule is evaluated **once for the
whole fleet**, not once per device.

`POST /api/reports/compliance-summary/` — body
`{format, group_by, site_ids, include_score_breakdown, as_of}`.

## Operations report (daily / weekly / monthly / quarterly)

The Operations report covers a reporting **period** (default: yesterday, UTC).
Daily shows raw events; weekly/monthly/quarterly add period-over-period
comparison and per-day trend charts. Sections:

- **Device security events** — authentication **failures reported BY network
  devices**, mined from the normalized syslog in OpenSearch (`netpulse-logs-*`).
  Grouped by user with brute-force / multi-device flags; a *success-after-
  failures* finding flags ≥3 failures on the same device shortly before a
  success (possible breach). Collector-side `host key verification` noise and
  RADIUS/TACACS *successes* are excluded. Degrades to a "forward TACACS+/RADIUS
  syslog" note when OpenSearch is unavailable.
- **Availability** — outages reconstructed from `device-unreachable` AlertEvent
  history (so same-day recoveries are captured), fleet availability %, a 24h
  outage timeline.
- **Compliance status** — fleet score + grade (as-of `ComplianceTemplateResult`,
  no live calls), day-over-day trend (degraded/improved), unsaved-config device
  list (run `write memory`), and devices below threshold with top issues.
- **Service check failures** — `ServiceCheck`/`CheckResult` down/degraded,
  grouped per check with duration stats and **correlation to device outages**.
- **Config changes** — every changed `DeviceConfig` with the **full unified
  diff**, `lines_added`/`removed`, site/role/platform, backup timestamps.
- **Collection health** — per-status breakdown (success/unchanged/timeout/…) +
  success rate + failed devices.
- **Agent health & alerts** — agents online, alerts by severity, critical list.
- **spane access events** — spane's OWN audit (`AuditLog`): failed logins,
  after-hours logins, new source IPs, admin/config actions (not routine logins).

The redesigned PDF leads with a one-page **executive summary** (four colour-coded
stat boxes + one-line section summaries + trend charts), then **conditional**
detail pages (compliance always; the rest only when they have something to
report), with a branded header/footer ("CONFIDENTIAL — Internal Use Only") on
every page. HTML mirrors the sections; the **Preview** button shows config diffs
in-browser before download.

- `POST /api/reports/ops/` — body `{period, end_date, format, site_ids}`
  (`period` = `daily|weekly|monthly|quarterly`).
- `POST /api/reports/daily-ops/` — body `{format, date, site_ids}` (still works).

## Scheduling

Schedule recurring delivery (daily / weekly / monthly / **quarterly** at a UTC
hour) to a list of email recipients; the report period rides in the schedule's
`parameters`. The `run_scheduler` loop checks for due schedules each tick
(hour-gated + same-day-deduped) and emails the rendered artifact via the SMTP
integration with a "Quick Summary" body. Quarterly fires on the 1st of
Jan/Apr/Jul/Oct.

- `GET|POST /api/reports/{compliance-summary,daily-ops}/schedule/`
- `PATCH|DELETE /api/reports/schedules/{id}/`
- `GET /api/reports/` — generation history · `GET /api/reports/{id}/download/`.

## Managing report history

- `DELETE /api/reports/{id}/` removes a report (and its stored file).
- `POST /api/reports/bulk-delete/` `{ids:[…]}` removes many at once.
- The Recent Reports table has per-row and select-all checkboxes with a bulk
  "Delete N selected" action.

## Limitations

- Compliance Summary role-consistency VLAN checks contact live devices over REST
  (existing behavior), so a full-fleet PDF can take ~1 minute.
- Device security events depend on devices forwarding auth syslog to spane
  (UDP/TCP 514); without it the section shows a setup note.
- Trend/aggregate accuracy is bounded by retained history (syslog in OpenSearch,
  `ComplianceTemplateResult`, outage AlertEvents).
