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

## Daily Operations

A single day's operational signal (default: yesterday, UTC):

- **Security** — login failures/successes, after-hours logins, new source IPs.
- **Availability** — outages (from `Device.unreachable_since`), fleet availability %.
- **Compliance events** — new/resolved failures, currently-failing devices.
- **Config changes** — every changed `DeviceConfig`, with the **full unified diff**
  (computed on the fly from previous vs current config), `lines_added`/`removed`,
  site/role/platform, and `previous_backup_at`/`current_backup_at`.
- **Collection health** — success rate + failed devices.
- **Agent health** + **alerts summary** (by severity / type).

The PDF renders a config-change summary table followed by per-device
colour-coded diffs (green adds, red removes, grey context, Courier). The HTML
format mirrors this. The **Preview** button on the Reports page shows the same
config diffs in-browser (expandable, syntax-highlighted) before you download.

`POST /api/reports/daily-ops/` — body `{format, date, site_ids}`.

## Scheduling

Schedule recurring delivery (daily / weekly / monthly at a UTC hour) to a list
of email recipients. The `run_scheduler` loop checks for due schedules each tick
(hour-gated + same-day-deduped) and emails the rendered artifact via the SMTP
integration with a "Quick Summary" body.

- `GET|POST /api/reports/{compliance-summary,daily-ops}/schedule/`
- `PATCH|DELETE /api/reports/schedules/{id}/`
- `GET /api/reports/` — generation history · `GET /api/reports/{id}/download/`.

## Limitations

- Compliance Summary role-consistency VLAN checks contact live devices over REST
  (existing behavior), so a full-fleet PDF can take ~1 minute.
- Daily-ops downtime is derived from `Device.unreachable_since` (no discrete
  outage-history table yet) — start-of-outage is accurate; intra-day recovery is
  approximate.
