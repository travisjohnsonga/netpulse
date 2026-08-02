# Alert dispatch & notification channels

spane records every alert as an `AlertEvent` (firing → resolved). The **dispatch
layer** delivers those transitions to the `AlertChannel`s an operator has
configured (email, Microsoft Teams, webhook, Slack, PagerDuty).

## How it's wired

`apps/alerts/dispatch.py` is the single choke point. It is invoked automatically:

- **Firing / save-based resolve** — an AlertEvent `post_save` signal
  (`apps/alerts/signals.py`) fires `dispatch_event(event, "firing")` on creation
  and `dispatch_event(event, "resolved")` when an event is saved into the
  RESOLVED state. Dispatch is deferred to `transaction.on_commit`, so it only
  runs for events that actually persist and the network I/O happens outside the
  alert-creating transaction.
- **`.update()`-based auto-resolve** — `resolve_matching()` (used by the
  reachability monitor, check engine, interface monitor) and the bulk-resolve API
  bypass signals, so they call `notify_resolved()` explicitly.

Because the signal handler is connected in every process that writes AlertEvents
(reachability monitor, check engine, scheduler, stream-processor, …), **all alert
sources route through the same dispatch** with no per-source wiring.

## Matching channels

For an event, dispatch considers:

- channels linked to the event's rule (`AlertRule.channels`), **plus**
- any active channel flagged `config.all_alerts: true` (a global channel).

Each candidate is then filtered by:

- **severity threshold** — `config.min_severity` (`info`<`low`<`medium`<`high`<`critical`); the event's severity must be ≥ it.
- **routing** — `config.match`, a dict of label→value(s); every entry must match the event's `labels`.
- a covering **maintenance window** suppresses *firing* notifications (recovery
  notifications always go out).

## Debounce / dedup

An alert is notified **once per FIRING transition and once per RESOLVED
transition**. Dispatch atomically claims the transition by stamping
`AlertEvent.fired_notified_at` / `resolved_notified_at` with a conditional
`UPDATE`; a second dispatch (a re-save, a flapping alert, or the signal + resolve
path overlapping) is a no-op. A flapping alert therefore cannot spam channels.

## Failure handling

Each channel send is retried with backoff (`ALERT_DISPATCH_MAX_ATTEMPTS`,
`ALERT_DISPATCH_BACKOFF_S`). A send failure is logged and isolated — it never
raises, never blocks the other channels, and never crashes the alert engine.

## Channel configuration

`AlertChannel.config` (JSON) conventions:

| type      | keys |
|-----------|------|
| `email`   | `recipients: []` (required), optional `from`, `reply_to`, `min_severity`, `match`, `all_alerts` |
| `teams`   | `webhook_url` (secret), `card_format: "adaptive"` (default) or `"messagecard"` |
| `webhook` | `url` (secret), optional `headers: {}` |
| `slack`   | `webhook_url` (secret) |
| `pagerduty` | `routing_key` (secret) |

**Secrets** (Teams/webhook URLs, PagerDuty routing keys) are moved to OpenBao at
`netpulse/alerts/channels/{id}` on save and never stored in PostgreSQL or returned
in API responses. When OpenBao is disabled (dev/test) they stay in `config`.

## Testing a channel

- **Per channel:** `POST /api/alerts/channels/{id}/test/` sends a synthetic test
  notification through that one channel and returns `{ok, detail}`.
- **End-to-end:** `python manage.py fire_test_alert --severity critical
  --title "Dispatch test" [--channel <id>] [--resolve]` creates a real AlertEvent
  so the full signal → dispatch → notifier path runs (and, with `--resolve`, the
  recovery path too).

## Settings

| setting | default | purpose |
|---------|---------|---------|
| `ALERT_DISPATCH_ENABLED` | `true` | master switch (off in the test suite) |
| `ALERT_DISPATCH_MAX_ATTEMPTS` | `2` | per-channel send attempts |
| `ALERT_DISPATCH_BACKOFF_S` | `2.0` | base backoff seconds between attempts |
| `FRONTEND_BASE_URL` | `""` | used to build the "View in spane" deep link |

## Adding a channel type

Write a `Notifier` subclass in `apps/alerts/notifiers/` and decorate it with
`@register("<channel_type>")`; the dispatcher looks it up by
`AlertChannel.channel_type`. `send(channel, payload)` returns `(ok, detail)` and
must never raise.
