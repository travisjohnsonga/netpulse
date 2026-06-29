"""
Alert dispatch — the layer that actually delivers AlertEvents to channels.

This is the single choke point every alert source routes through (via the
post_save signal in signals.py for created/saved events, and via resolve.py for
``.update()``-based auto-resolutions). Given an AlertEvent + transition it:

  1. Atomically *claims* the transition (stamps ``fired_notified_at`` /
     ``resolved_notified_at``) so a flapping/re-saved alert is notified exactly
     once per FIRING and once per RESOLVED — debounce/dedup (#5).
  2. Resolves the matching channels (active, severity threshold met, routing).
  3. Renders one AlertPayload and hands it to each channel's notifier.

Failure isolation (#6): a per-channel send failure is logged and retried with
backoff, never raises, and never blocks the other channels or the caller.
Dispatch is best-effort and swallows all exceptions — an alert engine must keep
running even when SMTP/Teams is down.
"""
from __future__ import annotations

import logging
import time

from django.conf import settings

from .payload import FIRING, RESOLVED, SEVERITY_ORDER, build_payload

logger = logging.getLogger(__name__)


def _enabled() -> bool:
    return getattr(settings, "ALERT_DISPATCH_ENABLED", True)


def _claim(event, transition: str):
    """
    Atomically mark this transition as notified. Returns True if *this* call
    won the claim (so it should send), False if it was already
    notified/claimed elsewhere. Uses a conditional UPDATE so concurrent
    dispatchers don't double-send.
    """
    from django.utils import timezone

    from .models import AlertEvent

    field = "fired_notified_at" if transition == FIRING else "resolved_notified_at"
    now = timezone.now()
    claimed = (AlertEvent.objects
               .filter(pk=event.pk, **{f"{field}__isnull": True})
               .update(**{field: now}))
    return bool(claimed)


def _channel_min_order(channel) -> int:
    cfg = channel.config or {}
    raw = (cfg.get("min_severity") or cfg.get("severity_threshold") or "info")
    return SEVERITY_ORDER.get(str(raw).lower(), 0)


def _routing_matches(channel, payload) -> bool:
    """A channel with a ``config.match`` dict only fires when every label in it
    matches the event's labels (value may be a single value or a list)."""
    cfg = channel.config or {}
    match = cfg.get("match")
    if not isinstance(match, dict) or not match:
        return True
    labels = payload.labels or {}
    for key, want in match.items():
        have = labels.get(key)
        if isinstance(want, (list, tuple, set)):
            if have not in want:
                return False
        elif str(have) != str(want):
            return False
    return True


# Alert types that are UI/audit *visibility* events: the AlertEvent is always
# created (Alerts list + audit trail), but they do NOT notify by default — paging
# people for a routine config diff is noise. The generation-vs-notification split
# applied per type. A channel opts in per-type via config.notify_types, e.g.
# {"notify_types": ["config_changed"]}. Override the default set with the
# ALERT_UI_ONLY_TYPES setting if needed.
DEFAULT_UI_ONLY_TYPES = frozenset({"config_changed"})


def _ui_only_types() -> frozenset:
    from django.conf import settings
    override = getattr(settings, "ALERT_UI_ONLY_TYPES", None)
    return frozenset(override) if override is not None else DEFAULT_UI_ONLY_TYPES


def _type_notifies(channel, alert_type: str) -> bool:
    """A UI-only/audit alert type notifies a channel ONLY if that channel
    explicitly opts in via config.notify_types. All other types notify normally."""
    if not alert_type or alert_type not in _ui_only_types():
        return True
    allow = (channel.config or {}).get("notify_types") or []
    return alert_type in allow


def matching_channels(event, payload) -> list:
    """Active channels for this event: those linked to its rule plus any channel
    flagged ``config.all_alerts`` (global), filtered by severity threshold,
    routing, and per-type UI-only suppression. Deduped by id."""
    from .models import AlertChannel

    candidates: dict = {}
    if getattr(event, "rule_id", None):
        for ch in event.rule.channels.filter(is_active=True):
            candidates[ch.pk] = ch
    for ch in AlertChannel.objects.filter(is_active=True):
        cfg = ch.config or {}
        if cfg.get("all_alerts") or cfg.get("global"):
            candidates.setdefault(ch.pk, ch)

    # For a delivery-failure meta-alarm, never notify the dead channel about itself.
    excluded_id = None
    if payload.alert_type == DELIVERY_FAILURE_TYPE:
        excluded_id = (payload.labels or {}).get("failed_channel_id")

    event_order = SEVERITY_ORDER.get(payload.severity, 0)
    out = []
    for ch in candidates.values():
        if excluded_id is not None and ch.pk == excluded_id:
            continue
        if event_order < _channel_min_order(ch):
            continue
        if not _routing_matches(ch, payload):
            continue
        if not _type_notifies(ch, payload.alert_type):  # UI-only/audit types: opt-in only
            continue
        out.append(ch)
    return out


# Meta alert type for a notification-delivery failure (cross-channel alarm).
# Guarded against recursion: a delivery-failure alert never alarms about itself.
DELIVERY_FAILURE_TYPE = "notification_delivery_failed"


def _record_delivery(channel, payload, ok: bool, detail: str, attempts: int) -> None:
    """Write a NotificationLog row for one delivery attempt — the source of truth
    for 'did it deliver?'. Skips test sends (no event_id). Never raises."""
    if not getattr(payload, "event_id", None):
        return
    try:
        from django.db import transaction

        from .models import NotificationLog
        # Own savepoint: if the event was deleted mid-dispatch (FK violation), the
        # failed INSERT rolls back in isolation and never breaks the caller's
        # transaction — logging a delivery must never break dispatch.
        with transaction.atomic():
            NotificationLog.objects.create(
                event_id=payload.event_id, channel=channel,
                channel_name=getattr(channel, "name", "") or "",
                channel_type=channel.channel_type, transition=payload.transition,
                status=NotificationLog.Status.SENT if ok else NotificationLog.Status.FAILED,
                attempts=attempts, detail=(detail or "")[:2000],
            )
    except Exception as exc:  # noqa: BLE001 — logging delivery must never break dispatch
        logger.warning("could not record delivery for event %s: %s",
                       getattr(payload, "event_id", "?"), exc)


def send_to_channel(channel, payload) -> tuple[bool, str]:
    """Send one payload to one channel with bounded retry/backoff. Records the
    outcome to NotificationLog. Never raises."""
    from .notifiers import get_notifier

    notifier = get_notifier(channel.channel_type)
    if notifier is None:
        detail = f"no notifier for channel type {channel.channel_type!r}"
        _record_delivery(channel, payload, False, detail, 0)
        return False, detail

    attempts = max(1, int(getattr(settings, "ALERT_DISPATCH_MAX_ATTEMPTS", 2)))
    backoff = float(getattr(settings, "ALERT_DISPATCH_BACKOFF_S", 2.0))
    last_detail = ""
    used = 0
    ok = False
    for attempt in range(1, attempts + 1):
        used = attempt
        try:
            ok, detail = notifier.send(channel, payload)
        except Exception as exc:  # noqa: BLE001 — a notifier must never crash dispatch
            ok, detail = False, str(exc)
        last_detail = detail
        if ok:
            if attempt > 1:
                logger.info("channel %s (%s) delivered on attempt %d",
                            channel.pk, channel.channel_type, attempt)
            break
        logger.warning("channel %s (%s) send failed (attempt %d/%d): %s",
                       channel.pk, channel.channel_type, attempt, attempts, detail)
        if attempt < attempts and backoff > 0:
            time.sleep(backoff * attempt)
    _record_delivery(channel, payload, ok, last_detail, used)
    return ok, last_detail


def _fire_delivery_failure_alarm(failed_channel, payload) -> None:
    """Cross-channel meta-alarm: a channel persistently failed → fire an AlertEvent
    that notifies via the OTHER (healthy) channels — the surviving channel reports
    the dead one. Debounced per channel (Valkey), skips test sends and meta events
    (no alarm-on-alarm). Best-effort; never raises."""
    if not getattr(payload, "event_id", None) or payload.alert_type == DELIVERY_FAILURE_TYPE:
        return
    try:
        from django.core.cache import cache
        window = int(getattr(settings, "ALERT_DELIVERY_ALARM_WINDOW_S", 900))
        if not cache.add(f"notif_fail_alarm:{failed_channel.pk}", 1, window):
            return  # already alarmed for this channel within the window
        from .models import AlertEvent, AlertRule
        rule, _ = AlertRule.objects.get_or_create(
            name="Notification Delivery Failed",
            defaults={"severity": "high", "condition": {"meta": True}, "is_active": True})
        AlertEvent.objects.create(
            rule=rule, state=AlertEvent.State.FIRING,
            labels={"source": "alert_dispatch", "severity": "high",
                    "alert_type": DELIVERY_FAILURE_TYPE,
                    "failed_channel_id": failed_channel.pk,
                    "failed_channel": getattr(failed_channel, "name", "")},
            annotations={"severity": "high", "alert_type": DELIVERY_FAILURE_TYPE,
                         "title": f"Alert delivery failing: {getattr(failed_channel, 'name', '')}",
                         "message": (f"Notifications to “{getattr(failed_channel, 'name', '')}” "
                                     f"({failed_channel.channel_type}) are failing after retries. "
                                     f"Other channels are still delivering this notice.")})
    except Exception as exc:  # noqa: BLE001
        logger.warning("could not fire delivery-failure alarm for channel %s: %s",
                       getattr(failed_channel, "pk", "?"), exc)


def _suppressed_by_maintenance(payload) -> bool:
    """Suppress FIRING notifications during a maintenance window covering the
    device (resolved/recovery notifications always go out)."""
    if payload.is_resolved:
        return False
    try:
        from apps.alerting.maintenance import is_in_maintenance
        return is_in_maintenance(device_id=payload.device_id, severity=payload.severity)
    except Exception:  # noqa: BLE001
        return False


def dispatch_event(event, transition: str) -> dict:
    """
    Deliver an AlertEvent transition ("firing"/"resolved") to all matching
    channels. Idempotent per transition. Returns a small summary dict
    (best-effort; for tests/logging). Never raises.
    """
    summary = {"dispatched": False, "channels": 0, "sent": 0, "failed": 0, "reason": ""}
    if not _enabled():
        summary["reason"] = "disabled"
        return summary
    if transition not in (FIRING, RESOLVED):
        summary["reason"] = f"unknown transition {transition!r}"
        return summary
    try:
        if not _claim(event, transition):
            summary["reason"] = "already notified"
            return summary
        # Per-rule notify gate: the rule still GENERATES events (the AlertEvent
        # exists / shows in the UI), but notifications are off (observe-only).
        # Checked before refresh_from_db() so the signal's select_related("rule")
        # is reused (no extra query).
        if getattr(event, "rule_id", None) and not event.rule.notify_enabled:
            summary["reason"] = "rule_notify_disabled"
            return summary
        # Re-read so the payload reflects the just-stamped resolved fields, etc.
        event.refresh_from_db()
        payload = build_payload(event, transition)
        if _suppressed_by_maintenance(payload):
            summary["reason"] = "maintenance"
            return summary
        channels = matching_channels(event, payload)
        summary["channels"] = len(channels)
        failed_channels = []
        for ch in channels:
            ok, _detail = send_to_channel(ch, payload)
            summary["sent" if ok else "failed"] += 1
            if not ok:
                failed_channels.append(ch)
        # A persistent per-channel failure → cross-channel meta-alarm (debounced;
        # skips delivery-failure events themselves to avoid alarm-on-alarm).
        for ch in failed_channels:
            _fire_delivery_failure_alarm(ch, payload)
        summary["dispatched"] = True
        logger.info("dispatched alert %s [%s/%s]: %d channel(s), %d sent, %d failed",
                    event.pk, transition, payload.severity,
                    summary["channels"], summary["sent"], summary["failed"])
    except Exception as exc:  # noqa: BLE001 — dispatch must never crash the caller
        logger.warning("dispatch_event failed for event %s: %s", getattr(event, "pk", "?"), exc)
        summary["reason"] = "error"
    return summary
