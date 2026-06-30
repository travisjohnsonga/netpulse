# spane Alerting Architecture — Assessment & Target

> **Status: PARTIALLY BUILT — Phases 0–2 shipped in app-v0.7.0; Phases 3–6 still
> designed.** Originally an assessment (empirically verified on the running lab
> 2026-06-29); the **delivery-reliability** layer (Phase 1) and the
> **notification-control + per-device/server silencing** layer (Phase 2) are now
> **built and shipped in app-v0.7.0**, on top of the Phase-0 dispatch substrate
> (#149, app-v0.6.0). What remains designed-not-built is the **alerting engine**:
> escalation timers + ack (Phase 3), routing unification (Phase 4), and anti-storm
> — dependency suppression + grouping + flap/for-duration (Phase 5) — targeted at
> **v0.8.0**. Per-feature status is marked ✅ built / ❌ designed throughout.

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

> **✅ Largely RESOLVED in app-v0.7.0 (#152).** The dead-letter/audit gap, the
> alarm-on-the-alarm, and the delivery-health surface are now built:
> **`NotificationLog`** (per-attempt audit, queryable + `/notifications` UI), a
> **cross-channel meta-alarm** (delivery failure routes through the surviving
> channels), and **delivery-health in `GET /api/health/`**. **Still open:** the
> claim-before-send re-delivery edge for a transient *total* failure, and a true
> **async delivery queue / durable worker** (current delivery is on-commit +
> in-line retry). The original gap analysis is kept below for context.

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

## 4b. Suppression, escalation & ack — the models exist; the engine is Stage-1

**Verified core finding:** the schema for suppression, escalation, timers and ack is
**already modeled** in `apps/alerting` — the engine just doesn't process it. The
engine says so itself:

> `apps/alerting/engine.py`: *"On-call resolution, timed multi-step escalation and
> acknowledgement land in later stages — Stage 1 fires step 1 by email."*

…and `process_alert_event()` has **zero callers** (§1), so even Stage-1 doesn't run on
a real alert today. **The alerting focus is engine logic over an existing schema, not
new models.**

### Suppression (anti-fatigue / anti-storm)

| Mechanism | Modeled? | Enforced? | Notes |
|---|---|---|---|
| **Maintenance windows** | ✅ `MaintenanceWindow` (devices/sites M2M; empty scope = all) + `is_in_maintenance()` | **Partial** | #149 dispatch + check engine + reachability already call `is_in_maintenance` to suppress *firing*. Not honored: the `AlertRoute.suppress_during_maintenance` flag (routing dormant) and resolve-notification suppression. |
| **Dependency suppression** `suppress_if_parent_down` | ✅ flag on `AlertRoute` (default True) | ❌ **not built** | THE key anti-storm feature: upstream switch down → suppress the 50 downstream alerts, send one "switch down." **Needs device→upstream resolution** — there is **no `Device.parent`**; topology exists (`TopologyLink`, `ManualTopologyLink`, `LLDPNeighbor`) but nothing designates an uplink or walks "this dependent is down *because* its parent is." Needs an explicit uplink/parent field or a topology-walk against reachability state. |
| **Dedup / grouping** (storm → one notification) | ❌ **not modeled** | ❌ | No grouping key, no storm digest. N related events = N notifications. |
| **Manual silence / mute / snooze** | ✅ `AlertAcknowledgement.snoozed_until` | ❌ **not enforced** | "Silence this for 2h" = ack-with-snooze; the field exists, nothing re-evaluates it. |

### Escalation & timers (don't-miss-a-critical)

| Mechanism | Modeled? | Enforced? | Notes |
|---|---|---|---|
| **Timed multi-step escalation** | ✅ `EscalationStep.step_number` + `delay_minutes` | ❌ engine fires **step 1 only** | After step 1, wait `delay_minutes` → if unacked → step 2 → …. Needs a timer that advances unacked escalations. |
| **Reminder / re-notify** | ✅ `EscalationPolicy.repeat_interval_minutes` | ❌ | Re-notify every N min until acked/resolved, so a critical isn't silently ignored. |
| **Ack halts escalation** | ✅ `AlertAcknowledgement` | ❌ | Ack must stop step-advancement + reminders for the current firing. |
| **The timer mechanism** | — | ❌ **missing** | Needs a periodic evaluator ("which firing-unacked alerts are due for the next step / reminder?"). **Infra exists:** the single `run_scheduler` loop (300s tick) is the home — add an `escalation_tick` task. **Do NOT add Celery** (present but unused; one scheduler only). |

### Acknowledgement — stop re-alerting (scope is the design)

- **Ack → halt re-notification + escalation for the *current firing only*.** No more
  reminders, no more steps. Ack = "I've got this."
- **Re-fire is fresh.** If the alert resolves then later re-fires, that's a **new**
  `AlertEvent` → it notifies again. Ack mutes the *incident*, never the device
  permanently.
- **Stays visible.** Acked ≠ resolved ≠ gone — it remains in the Alerts list as
  *acknowledged* (someone owns it, notifications halted). The serializer already
  derives this (firing + has `AlertAcknowledgement`).
- **Where to ack:** the UI button exists (`acknowledge` / `bulk-acknowledge`, which
  already cancels *pending* `AlertNotification`s) — this is the **on-prem** ack
  surface and works everywhere.
  - **Ack-from-notification (email ack-link / Teams card `Action.Http`) is a
    SaaS-tier feature, NOT on-prem.** It requires the app to be **publicly reachable**
    to record the ack; an on-prem (non-public) deployment would need a VPN to hit the
    link, so it's not reliable there. **On-prem ack stays in-app.** Build the
    notification-ack surfaces when the **SaaS tier** (the hosted `spane.app` offering)
    is in scope.
  - *Possible middle-ground (SaaS-era complexity, later):* a narrowly-exposed
    **signed ack-token endpoint**, or **ack via a Teams bot action** that routes
    through Teams' own infra rather than a direct link back to spane. Out of scope
    until the SaaS tier.

### Generation vs. external notification — make the split an explicit control

> **✅ BUILT in app-v0.7.0 (#151/#155).** The split is now an explicit control:
> per-channel `min_severity`, per-type **UI-only** types, and a **per-rule notify
> toggle** ("observe mode") — generation always happens, notification is gated
> independently. Design intent below.

The architecture **already separates** detection from delivery (AlertEvent creation is
independent of dispatch). Make that an explicit, controllable product feature:

- **Alert generation (the UI Alerts list) ALWAYS happens** — the in-app list is the
  source of truth for what's firing; never suppress the *record*. You can always *see*
  every alert regardless of notification settings. (Suppression above mutes the
  *notification*, not the `AlertEvent` — keep it that way.)
- **External notification is independently toggleable**, controlled separately from
  generation: an alert can show in the UI and **not** page, or show **and** page.
- **Control granularity** (existing hooks): **global** master notify on/off;
  **per-rule** (`AlertRule.is_active` / a new `notify` flag — generates UI alerts but
  doesn't page); **per-severity** (per-channel `min_severity` already — page only
  high/critical, everything still generates); **per-route/channel**
  (`AlertChannel.is_active`, `config.match`, `TeamMember.notify_*`).
- **Use case — "observe mode":** stand up a new monitor generating UI alerts only,
  confirm it isn't noisy, *then* enable notifications. Tune paging to what matters
  without going blind — the anti-fatigue control that complements suppression.

### Per-device/server silencing — three forms (network devices AND servers)

> **✅ Forms 1–2 BUILT in app-v0.7.0 (#156):** `Device.alerting_enabled`
> (permanent observe-only) and `Device.silenced_until` (auto-expiring timed mute),
> both applying to network devices AND agent servers, checked on the notify path
> (the `AlertEvent` is still generated). **❌ Form 3 (uniform `MaintenanceWindow`
> enforcement on notify + resolve) still designed**, as is the optional
> `environment` field. Design table below.

The generation-vs-notification split applied per *target* — three forms covering
the spectrum (permanent / ad-hoc-timed / scheduled). **Existing pieces to build on,
not duplicate:** `Agent.liveness_alerts_enabled` (default True, but **liveness-ONLY**
— suppresses just Agent-Offline; too narrow); `MaintenanceWindow` (scheduled,
device/site scope + recurrence — **modeled, engine doesn't enforce**);
`AlertAcknowledgement.snoozed_until` (a timed-mute precedent). **No `environment`
field on `Device` today.**

| Form | What | Backend | UI |
|---|---|---|---|
| **1. Permanent disable** — "monitor, never page" (dev/test/qa boxes) | `alerting_enabled` (default True). False → still **generates** AlertEvents (UI/telemetry visibility) but **never notifies**. | New `Device.alerting_enabled` — the broad generalization of `liveness_alerts_enabled` (which folds in / becomes a sub-toggle). Dispatch checks it → skip notify, keep the event. | "Alerting: On / Observe-only" toggle on device + server detail. |
| **2. Timed silence** — "mute for N, I'm patching" | `silenced_until` timestamp; while future → suppress **notifications** (events still generate, shown "silenced"). **Auto-expires → alerting auto-resumes.** | New `Device.silenced_until` (mirror `snoozed_until`). Dispatch checks `now < silenced_until`. **Auto-expiry is the safety property** — a maintenance mute can't silently become permanent and mask a later real outage. | "Silence alerts" quick action → duration prompt (1h/4h/8h/24h/custom); show countdown + allow early un-silence. |
| **3. Maintenance windows** — scheduled/recurring | the scheduled cousin of #2; `MaintenanceWindow` already models device/site scope + recurrence. | **Enforce** it (the §4b gap) — honored for #149 *firing* via `is_in_maintenance`; extend uniformly to the notify decision + resolve-suppression. | Existing window UI; surface "in maintenance" on the device. |

**Invariant (all three):** they suppress the **notification**, never the **AlertEvent
generation** — the Alerts list + telemetry always show the device's true state
(someone looking sees it; nobody gets paged). The generation-vs-notification split,
keyed per target. **All three apply to both `device_kind=network_device` and
`server`** (same `Device` flags → one mechanism for the whole fleet).

**Optional enabler — `environment` field (dev/test/qa/prod):** tag a device/server's
environment and drive policy from it ("dev = observe-only by default") instead of
toggling each box. The per-device flags work without it; environment-based policy
**scales** for many dev boxes — a default-policy layer over the per-device flags.

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

- **Phase 1 — Delivery reliability (HIGHEST) — ✅ BUILT (app-v0.7.0, #152/#154):**
  a **`NotificationLog`** (one row per delivery attempt: event, channel, status,
  detail, ts) → queryable audit + the `/notifications` UI page + delivery-health
  endpoints folded into `GET /api/health/`; **cross-channel meta-alarm** (a channel
  failing raises an alert routed through the *surviving* channels); per-channel
  **retry/backoff** and **failure isolation**. *Silent failure is now observable.*
  *(Still designed: a true async delivery queue / dead-letter store — current
  retry is in-line; see Cross-cutting.)*

- **Phase 2 — Silencing & the generation/notification split — ✅ BUILT (app-v0.7.0,
  #151/#155/#156):** "UI alert generation" and "external notification" are now an
  explicit, multi-level control — per-channel `min_severity`, per-type UI-only
  (audit events), a **per-rule notify toggle**, and **per-device/server silencing**
  (`alerting_enabled` permanent observe-only + `silenced_until` auto-expiring timed
  mute, for network devices AND servers). None suppress the `AlertEvent` record —
  only the notification. *(Still designed: uniform maintenance-window enforcement
  on the resolve path, and an `environment` dev/test/prod policy field.)* Original
  design follows. Three **per-device/server** silencing forms
  (apply to network devices AND servers, generate-but-don't-notify): **(1) permanent
  disable** `Device.alerting_enabled` (generalizes `liveness_alerts_enabled`) for
  dev/test boxes; **(2) timed silence** `Device.silenced_until` (mute-for-N, **auto-
  expiring** so a patch-mute can't mask a later outage); **(3) maintenance windows**
  (enforce the existing `MaintenanceWindow` uniformly on the notify path + resolve).
  Plus **global** + **per-rule `notify` toggle** ("observe mode") on top of the
  existing per-severity (`min_severity`) / per-channel hooks, and **manual snooze**.
  None suppress the `AlertEvent` record — only the notification. *(Optional enabler:
  an `environment` field to drive policy by dev/test/qa/prod.)* *Cheap, high-value,
  prevents fatigue and tames planned maintenance while keeping full visibility.*
  (See §4b.)

- **Phase 3 — Escalation timers + ack (don't-miss-a-critical):** add an
  **`escalation_tick`** task to the existing `run_scheduler` loop that advances
  firing-unacked alerts — **timed multi-step escalation** (`EscalationStep.delay_minutes`),
  **reminders** (`EscalationPolicy.repeat_interval_minutes`), **ack halts escalation +
  reminders** (current-firing scope; re-fire notifies fresh), and on-call `Shift`
  resolution. Ack here is the **in-app** button (works on-prem + SaaS). All delivering
  via the Phase-0/1 `send_to_channel`. (Models exist; this is the engine Stage-1 →
  Stage-2 work — see §4b.) Runs on the **shared evaluation loop (§8)** — **No Celery;
  use `run_scheduler`.**
  - **Ack-from-notification (email ack-link / Teams card action) is deferred to the
    SaaS tier** — it needs the app publicly reachable; on-prem ack stays in-app (§4b).

- **Phase 4 — Unify routing onto the substrate:** one `Route` concept (extend
  `AlertChannel` matching or a thin routing layer) matching severity/source/**kind**/
  **site**/**device**/tags; link routes → channels (+ team/policy); wire it into the
  live fire path, replacing ad-hoc `rule.channels`+`all_alerts`; retire
  `process_alert_event`'s own sender (route → `send_to_channel`). Collapses the two
  systems. (Includes the *Expanded alert routing* roadmap item: `match_device_kind`
  via `device_kind` #142 + `match_devices` M2M.)

- **Phase 5 — Anti-storm (dependency suppression + grouping):** `suppress_if_parent_down`
  — needs a device→uplink model (explicit parent FK or a topology-walk over
  `LLDPNeighbor`/`TopologyLink` against reachability) so one "switch down" replaces 50
  downstream pages; plus **dedup/grouping** — leading-edge + extend, global
  UI-configurable key, channel-aware folding, `AlertGroup` model (**full design in
  §7**). *The key anti-storm work — not modeled yet; runs on the shared loop (§8).*

- **Phase 6 — Enterprise:** ITSM (ServiceNow/Jira) as channel types, SMS/PagerDuty
  escalation, per-team kind/site/device routing UI, alerting reports.

**Cross-cutting:** async delivery queue (decouple send from the alert-writer process);
metrics on fire→deliver latency and failure rate.

---

## 6. Flags (decisions to make now)

1. **Two-system unification.** Recommend **#149 as the single delivery substrate**,
   `apps/alerting` reduced to the routing/escalation layer that *calls* it; retire the
   dormant duplicate sender. (The "Option A" instinct is right; the live/dormant roles
   are inverted from the initial read — `alerting` is the dormant one.)
2. **Reliability / failure-handling (Phase 1).** Silent dispatch failure is the #1 eval
   risk. Dead-letter + audit + alarm-on-failure + redelivery are not optional.
3. **Anti-fatigue without going blind (Phase 2).** The generation-vs-notification split
   + observe-mode + suppression enforcement (maintenance, snooze) is cheap and high-value
   — see everything in the UI, page only for what matters.
4. **Escalation/ack engine (Phase 3).** The schema exists; the engine is Stage-1 (fires
   step 1, no timers, no ack-handling). The timer-driven `run_scheduler` loop + ack→halt
   is the work that makes "criticals can't be silently missed" real.
5. **Anti-storm — dependency suppression (Phase 5).** `suppress_if_parent_down` is
   modeled but unbuilt and needs a device-uplink/topology model; it's THE feature that
   stops one upstream failure becoming 50 pages.
6. **Routing expansion (Phase 4).** Per-kind/site/device routing (unified network+server)
   lands when routing graduates onto the substrate.
7. **One shared evaluation loop, not four timers (§8).** for-duration, flap, grouping
   (§7) and escalation are all time-driven re-evaluations — build the spine once (with
   the first feature that needs it: for-duration) on `run_scheduler`, never Celery.
8. **TV wall as the externally-independent fallback tier (§9).** The NOC wall is the
   always-watched, WebSocket-only surface that survives an email/Teams outage and
   shows "delivery degraded" — the fourth failure-visibility tier. Builds on existing
   TV infra; delivery-failure display reads PR #152's delivery-health (built),
   critical-takeover wants grouping (§7).

---

## 7. Design — alert grouping (storm collapse)

> Lands in **Phase 5** (anti-storm), built **after** the for-duration/flap features
> that establish the shared evaluation loop (§8). Recorded here as the committed
> design.

**Strategy — leading-edge + extend (NOT hold-then-send).** The first alert of a group
**notifies immediately** (no delay on the first / a critical) and **opens a group**
(thread/card); subsequent matching alerts **fold into it** instead of spawning new
notifications; the group emits **batched updates** as it grows and **resolves** when
its members clear. The storm collapses to one thread *without* delaying the leading
alert. (The PagerDuty / Alertmanager model.)

**Grouping key — global, UI-configurable.** ONE key for all alerts (simplest); the
operator picks which fields compose it — no code change. **Default = `site` +
`alert_type`** → "Site X: 12 unreachable." Configurable dimensions: `site`,
`alert_type`/`source`, `severity`, `device_kind`, `agent`/`device` — all already on
`AlertEvent.severity` + `labels`, so the key is built from those.

**Channel-aware folding** — fold to what the channel supports:
- **Teams / Slack** (editable/threadable) → **update** the existing card / post into the
  thread ("now 12, was 5").
- **Email** (can't edit a sent message) → a **leading email** + **throttled digest
  updates** (batched "now 12", never one-per-device).

**Timers (all on the shared loop, §8):** **leading send** (immediate, or a ~5s
**micro-coalesce** to absorb a simultaneous burst into the first notification) +
**`group_interval`** (flush batched updates every ~30–60s, not per-alert) +
**`repeat_interval`** (still-firing reminders). The group **window EXTENDS** while
alerts keep arriving and **settles** when quiet.

**Data model:** add **`AlertGroup`** (`key`, members, `state` =
collecting/notified/resolved, `opened_at`/`last_update_at`/`resolved_at`, + per-channel
message/thread ids so updates can edit the right card) and a **`group` FK on
`AlertEvent`**. On fire: compute key → find-or-open group → attach → leading-send or
fold per the group's state. On resolve: detach; when the last member clears, resolve
the group (and its card/thread). **Depends on §8.**

---

## 8. Design — the shared evaluation loop (one spine, not four timers)

Several features are **time-driven re-evaluations of firing state** and **must share
one periodic loop**:
- **for-duration** — "has this condition held ≥ N min?" (fire-after-sustained).
- **flap detection** — "flapping over the window?" (suppress / annotate).
- **grouping** (§7) — "flush batched updates / has the window settled?"
- **escalation** (Phase 3) — "firing-unacked alert due for its next step / reminder?"

**Mechanism — `run_scheduler`, NOT Celery (confirmed).** The single authoritative
scheduler is the `run_scheduler` management-command loop (the `scheduler` service).
`Celery` / `django-celery-beat` are in requirements but **UNUSED** — do not add a
second scheduler. Add **one alerting evaluation task** (generalize the
`escalation_tick` referenced above into an **`alerting_tick`**) that, each pass,
advances *all* of the above over the firing set.

**Cadence caveat (important):** `run_scheduler`'s default `--tick` is **300s** — far
too coarse for grouping's ~5s coalesce / 30–60s `group_interval` and for tight
escalation timing. The loop already clamps to a 5s floor (`max(5, tick)`), so the
alerting evaluation needs a **dedicated faster cadence** (e.g. 15–30s): either run the
scheduler with a smaller `--tick` (affects all tasks) or give the alerting evaluation
its **own sub-loop/interval** inside the scheduler service. **Decide this when the
first loop-dependent feature (for-duration) lands — establish the spine once**, then
flap / grouping / escalation reuse it.

---

## 9. Design — TV dashboard as a notification tier (NOC wall)

> Design only. Builds on **existing** infra — the TV dashboards (`pages/tv/`:
> TVNetwork/Servers/Security/Compliance/Wireless + `TVRotate` rotation), live over
> the WebSocket `/ws/telemetry/`, and the alerts WS consumer
> (`apps/alerts/consumers.py`). This *surfaces alerts prominently on the
> always-watched wall*, not new push infra.

A NOC wall makes the TV a first-class **notification tier**, not just a status
display — specifically the **always-watched, externally-independent fallback** the
multi-tier failure design (§3) wanted.

**Why it's a real tier:**
- **Always-watched by design** → solves *"a header banner only works if someone's
  looking."* A critical on the wall **is** seen; the wall is the banner on a
  guaranteed-watched surface.
- **Independent path** → updates ride the **WebSocket**, not email/Teams (SMTP/
  webhook). It needs **no external service** (no SMTP, no Microsoft), so it **survives
  the outages that kill email/Teams** — when push delivery fails (the §3 / PR #152
  case) the wall still shows the alert *and* can show "⚠️ delivery degraded." The
  externally-independent fallback.
- **Attention-escalating modality** email can't do: flash, full-screen red, sound,
  and **take over the rotation** — a critical can interrupt and pin until acked.

**Phased build:**
1. **Alert overlay** — a persistent severity-coloured alert strip/panel on the TV
   dashboards (or a dedicated `TVAlerts` view): active firing alerts, counts, grouped
   summaries, live via the existing **alerts WS consumer** (`apps/alerts/consumers.py`).
   Every TV view shows current alerts. *(No new dependency.)*
2. **Critical takeover** — a critical (config: which severities) **interrupts
   `TVRotate`** and pins a full-screen alert until acked/resolved. **Grouping-aware:
   once grouping (§7) lands, the takeover pins the GROUP summary** ("CRITICAL: 12
   unreachable at Waco"), not 12 separate takeovers. **→ depends on §7 (grouping).**
3. **Delivery-failure display** — reads the **`/api/alerts/notifications/delivery-health/`
   endpoint built in PR #152** (and `notification_delivery` on `/api/health/
   infrastructure/`): when push delivery is degraded, the wall shows a prominent
   "⚠️ Alert delivery degraded — email/Teams failing." The TV becomes the fallback
   surface precisely when the push channels are the thing that's down.
   **→ depends on PR #152's delivery-health (BUILT).**
4. **Optional** — audio cue on a new critical (NOC wall with sound); ack-from-the-wall
   (with an input device) or ack-from-app clears the takeover.

**Dependencies (explicit):** overlay (1) needs only the existing alerts WS consumer;
takeover (2) wants **grouping (§7)** to pin group summaries instead of N takeovers;
delivery-failure display (3) reads **PR #152's delivery-health** (already built).
Where it fits §3: the in-app banner + cross-channel meta-alarm + `/api/health` are the
failure-visibility tiers — the TV wall adds a **fourth, always-watched,
externally-independent** one, and is the natural home for the "delivery degraded"
signal when email/Teams are the thing that's down.
