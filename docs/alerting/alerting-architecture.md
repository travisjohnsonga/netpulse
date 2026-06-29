# spane Alerting Architecture — Assessment & Target

> **Status: ASSESSMENT / design (NOT built).** Maps the current state (warts and
> all, empirically verified on the running lab 2026-06-29) and a phased target.
> Nothing here is implemented beyond what's explicitly marked *live*.

## 0. Why this matters

Alerting is make-or-break for the evaluation. If an event fires and alerting
misbehaves — doesn't fire, reaches the wrong people, hits only one channel, or
**fails silently** — the product fails the eval. The bar is **correct and
reliable**, not merely functional.

**Headline finding (verified, not assumed):** there are two alerting systems but
**only one actually fires.** `apps/alerts` (the #149 AlertChannel dispatch) is the
**live** path; `apps/alerting` (the routing/escalation engine) is **dormant** — its
`process_alert_event()` has **zero callers**. Proven by firing a real-path alert
and instrumenting both engines:

```
real alert (generic rule, real fire path):
  alerts.dispatch_event()         called 1×   ← fires
  alerting.process_alert_event()  called 0×   ← never fires
  AlertNotification rows created  0           ← alerting produced nothing
```

The three things to fix, in priority order: **(1) delivery reliability** (silent
dispatch failure = a missed page nobody knows about — the worst case), **(2)
two-system unification** (collapse onto one substrate), **(3) routing expansion**
(per-team / kind / site / device).

---

## 1. The two systems (exact, verified)

| | `apps/alerts` (#149) | `apps/alerting` |
|---|---|---|
| **Models** | `AlertChannel` (config JSON, secrets→OpenBao), `AlertRule`, `AlertEvent` | `Team`, `EscalationPolicy`, `EscalationStep`, `AlertRoute`, on-call `Shift`, `ContactMethod`, `AlertNotification`, `MaintenanceWindow`, `AlertAcknowledgement` |
| **Channel types** | email, slack, **teams**, webhook, pagerduty | email, slack, discord (per-team `slack_webhook_url`/`discord_webhook_url`) — **no Teams, no pagerduty, no generic webhook** |
| **Routing** | `AlertRule.channels` M2M + `config.all_alerts` global + per-channel `min_severity` + `config.match` labels | `AlertRoute` match: severity / source / device_tags / check_types / **sites** (M2M); → first `EscalationPolicy` step |
| **Recipients** | per-channel `config.recipients` (explicit list) | on-call user / explicit `notify_user` / team members with `notify_email=True` |
| **Resolve / recovery** | **Yes** — fires on resolve too (recovery notification) | **No** — firing only |
| **Debounce** | `fired_notified_at` / `resolved_notified_at` (claim once per transition) | none |
| **Retry / failure isolation** | per-channel retry+backoff, one bad channel doesn't block others | none (one `send_email` per recipient) |
| **Escalation / on-call / ack** | none | modeled (steps, shifts, `AlertAcknowledgement`) |
| **Maintenance windows** | honors `alerting.is_in_maintenance` (suppresses firing) | **owns** `MaintenanceWindow` + `is_in_maintenance` |
| **Wired to the fire path?** | **YES** — `AlertEvent` `post_save` signal → `dispatch_event` | **NO** — `process_alert_event()` has zero callers; app has no signals |

**Which fires for a real alert today:** `apps/alerts` (#149), only. The sole *live*
contributions of `apps/alerting` are `is_in_maintenance` (suppression, reused by
#149 dispatch + check engine + reachability monitor) and `AlertAcknowledgement`
(the ack button on the Alerts page).

**Overlap / conflict (why two is a problem):**
- Both model "match an event → notify someone." Neither is complete: #149 has all
  the channel types + resolve + debounce + retry but only flat per-channel routing;
  `alerting` has on-call/escalation/ack/site-routing but no Teams/webhook/pagerduty,
  no resolve, no retry, and **isn't connected to anything**.
- Channel *types* (Teams/PagerDuty/Webhook) exist **only** in #149; escalation /
  on-call / per-route recipients exist **only** in `alerting`. They are two halves
  of one system.
- Risk if both were ever wired: **duplicate notifications** and split config
  (a Teams URL in one place, on-call in another). Today there's no duplication only
  because `alerting` is dormant.

---

## 2. Full alert lifecycle (current, end to end)

**Detection — where `AlertEvent`s are created (all sources):**
`apps/alerts/interface_monitor.py`, `apps/devices/.../run_reachability_monitor.py`,
`apps/checks/.../run_check_engine.py` (via `CheckResult`), `apps/agents/stability.py`,
`apps/agents/liveness.py`, `apps/agents/functional.py`,
`apps/telemetry/environment_poll.py` (PoE), `apps/circuits/scheduler.py`,
`apps/compliance/collector.py` (startup/config), `apps/compliance/os_policy.py`,
`apps/devices/hostname_check.py`, `apps/telemetry/.../run_stream_processor.py` (flow
threshold). Each builds an `AlertEvent` with `labels` (source, device_id, severity,
transition…) + `annotations` (title, message, severity).

**Pipeline (live path):**
```
AlertEvent.objects.create(...)            (firing)         ── many sources
        │  post_save signal (apps/alerts/signals.py), deferred to transaction.on_commit
        ▼
dispatch_event(event, "firing")           (apps/alerts/dispatch.py)
        │  claim fired_notified_at (atomic, once)           ← debounce
        │  maintenance-window suppression (firing only)
        ▼
matching_channels(event)                  rule.channels ∪ all_alerts, filter min_severity + config.match
        ▼
per channel: send_to_channel()            notifier by channel_type, retry+backoff, isolated failures
        ▼
delivery                                  SMTP (EmailSettings→OpenBao) / Teams webhook / slack / webhook / pagerduty

Recovery:  resolve_matching()/save → state=RESOLVED → dispatch_event(event,"resolved")
           (claim resolved_notified_at) → recovery notification ("✓ Resolved")
```

**Ack / escalation / re-notify:** `AlertAcknowledgement` records an ack and cancels
*pending* `AlertNotification`s — but those are only ever produced by the **dormant**
`process_alert_event`, so on a real alert there is **no escalation, no on-call, no
ack-driven re-notify** today. Ack is effectively cosmetic on the live path.

**Gaps / risks per stage:**
- *Routing:* flat (rule-link / all_alerts / label match). No team / kind / site /
  device routing on the live path (site routing exists only in dormant `AlertRoute`).
- *Recipient resolution:* per-channel static list; no on-call/team resolution live.
- *Dispatch:* runs **synchronously inside the alert-writing process** at `on_commit`
  (no async queue) — a slow SMTP/webhook briefly blocks that worker (bounded by
  timeouts).
- *Delivery:* no per-delivery audit row for #149 (only `fired/resolved_notified_at`
  + logs) → **no queryable "what was sent where, success/fail."**
- *Escalation:* none live.

---

## 3. Reliability gaps (CRITICAL — the eval risk)

**Silent dispatch failure is the worst case: a real alert fires, every channel send
fails (SMTP down, webhook 500), and nobody is paged AND nobody knows.** Current
state:

- **Have:** per-send retry (`ALERT_DISPATCH_MAX_ATTEMPTS`, backoff), per-channel
  failure isolation (one bad channel ≠ no notifications), warning logs.
- **Missing (must fix):**
  - **No dead-letter / failed-notification record.** A total failure is logged and
    gone. There is no table to inspect, no redelivery, no surfacing in the UI.
  - **No alert-on-notification-failure ("alarm on the alarm path").** If delivery is
    broken, nothing tells the operator.
  - **Claim-before-send means a hard failure for a transition is never re-sent.**
    `dispatch_event` stamps `fired_notified_at` *before* sending (to prevent spam);
    if all sends then fail, a later evaluation won't retry — the debounce guard
    blocks it. Good against flapping, bad against a transient total outage.
  - **No delivery health metric/surface** ("N notifications failing in the last
    hour"), no heartbeat that delivery works.
  - **Synchronous on-commit delivery** couples alert creation to channel latency; a
    hung endpoint degrades the producer. No durable queue / worker.

These are the **top-priority** items: an alerting system that can fail silently is
disqualifying for the eval.

---

## 4. What's missing for production / enterprise

| Capability | State |
|---|---|
| Escalation (no-ack-in-N-min → escalate) | Modeled in `alerting`, **not wired** |
| On-call schedules | Modeled (`Shift`), **not wired** |
| Dedup / grouping (alert storms: N devices down → N pages) | **None** |
| Maintenance windows | **Exists** (`alerting.MaintenanceWindow`, honored by #149 + check/reachability) — keep |
| Severity routing | Per-channel `min_severity` ✅ (#149); per-route severity not wired |
| Per-team / per-kind / per-site / per-device routing | Sites in dormant `AlertRoute`; kind/device = roadmap (see *Expanded alert routing*) |
| Ack / silence / snooze | `AlertAcknowledgement` (+`snoozed_until`) exists; no live effect (escalation off) |
| Notification audit / history | `AlertNotification` table exists but only the dormant engine writes it; #149 has **no per-delivery audit** |
| ITSM (ServiceNow / Jira) | Roadmap |
| SMS / PagerDuty escalation | PagerDuty channel exists (#149); no escalation logic |

---

## 5. Target architecture (one unified system, phased)

**One pipeline, one substrate:**
```
AlertEvent (detection)
   → Routing        match: severity / source / kind / site / device / tags  → selects channels + policy
   → Escalation     policy steps · on-call resolution · ack · re-notify
   → Dispatch       uniform notifiers: email / teams / slack / webhook / pagerduty / ITSM
   → Delivery       async queue · retry · dead-letter · per-delivery audit · alarm-on-failure
```

**Substrate decision (recommended): make `apps/alerts` (#149) the delivery substrate
and graduate `apps/alerting`'s routing/escalation concepts on top of it — retire the
dormant `process_alert_event` sender.** Rationale: #149 is already live, has every
channel type, resolve, debounce, retry, and OpenBao secrets. `alerting` contributes
the *routing/escalation/on-call* layer (which #149 lacks) but should **call
`dispatch.send_to_channel`** instead of its own `send_email`, so there is exactly one
delivery code path. **Pick one; do not grow a third.**

**Phasing (priority-ordered for the eval):**

- **Phase 0 — DONE (#149):** channel dispatch substrate (email/teams/slack/webhook/
  pagerduty), fire **and** resolve, debounce, per-channel retry, failure isolation,
  secrets in OpenBao, maintenance-window suppression. *Tactical:* scope channels via
  `all_alerts`/`min_severity`/`config.match` (done for the lab).

- **Phase 1 — Delivery reliability (HIGHEST):** add a **NotificationLog** (one row per
  delivery attempt: event, channel, status, detail, ts) → queryable audit + UI surface;
  **dead-letter** failed deliveries; **alarm-on-the-alarm** (a meta-alert when N
  consecutive deliveries fail or a channel is down); **redelivery** of transient total
  failures (decouple the debounce-claim from send success so a 0-of-N delivery is
  retried); a **delivery-health** metric/heartbeat. *Make silent failure impossible.*

- **Phase 2 — Unify routing onto the substrate:** one `Route` concept (extend
  `AlertChannel` matching or a thin routing layer) matching severity/source/**kind**/
  **site**/**device**/tags; link routes → channels (+ optional team/policy); wire it
  into the live fire path, replacing ad-hoc `rule.channels`+`all_alerts`; retire
  `process_alert_event`'s own sender (route → `send_to_channel`). Collapses the two
  systems. (Includes the *Expanded alert routing* roadmap item: `match_device_kind`
  via `device_kind` #142 + `match_devices` M2M.)

- **Phase 3 — Escalation + on-call:** wire `EscalationPolicy` steps, on-call `Shift`
  resolution, ack-driven stop/re-notify, snooze/silence — all delivering via the
  Phase-0/1 dispatch. (Models largely exist; the work is wiring + connecting to the
  one delivery path.)

- **Phase 4 — Storm control:** dedup/grouping (group by device/site/rule within a
  window), digest a storm into one notification with a count.

- **Phase 5 — Enterprise:** ITSM (ServiceNow/Jira) as channel types, SMS/PagerDuty
  escalation, per-team kind/site/device routing UI, alerting reports.

**Cross-cutting:** async delivery queue (decouple send from the alert-writer process);
keep + extend maintenance windows; metrics on fire→deliver latency and failure rate.

---

## 6. Flags (decisions to make now)

1. **Two-system unification.** Recommend **#149 as the single delivery substrate**,
   `apps/alerting` reduced to the routing/escalation layer that *calls* it; retire the
   dormant duplicate sender. (Travis's "Option A" instinct is right; the live/dormant
   roles are inverted from the initial read — `alerting` is the dormant one.)
2. **Reliability / failure-handling (Phase 1).** Silent dispatch failure is the #1
   eval risk. Dead-letter + audit + alarm-on-failure + redelivery are not optional.
3. **Routing expansion (Phase 2).** Per-kind/site/device routing (the unified
   network+server story) lands when routing graduates onto the substrate.
