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


def matching_channels(event, payload) -> list:
    """Active channels for this event: those linked to its rule plus any channel
    flagged ``config.all_alerts`` (global), filtered by severity threshold and
    routing. Deduped by id."""
    from .models import AlertChannel

    candidates: dict = {}
    if getattr(event, "rule_id", None):
        for ch in event.rule.channels.filter(is_active=True):
            candidates[ch.pk] = ch
    for ch in AlertChannel.objects.filter(is_active=True):
        cfg = ch.config or {}
        if cfg.get("all_alerts") or cfg.get("global"):
            candidates.setdefault(ch.pk, ch)

    event_order = SEVERITY_ORDER.get(payload.severity, 0)
    out = []
    for ch in candidates.values():
        if event_order < _channel_min_order(ch):
            continue
        if not _routing_matches(ch, payload):
            continue
        out.append(ch)
    return out


def send_to_channel(channel, payload) -> tuple[bool, str]:
    """Send one payload to one channel with bounded retry/backoff. Never raises."""
    from .notifiers import get_notifier

    notifier = get_notifier(channel.channel_type)
    if notifier is None:
        return False, f"no notifier for channel type {channel.channel_type!r}"

    attempts = max(1, int(getattr(settings, "ALERT_DISPATCH_MAX_ATTEMPTS", 2)))
    backoff = float(getattr(settings, "ALERT_DISPATCH_BACKOFF_S", 2.0))
    last_detail = ""
    for attempt in range(1, attempts + 1):
        try:
            ok, detail = notifier.send(channel, payload)
        except Exception as exc:  # noqa: BLE001 — a notifier must never crash dispatch
            ok, detail = False, str(exc)
        last_detail = detail
        if ok:
            if attempt > 1:
                logger.info("channel %s (%s) delivered on attempt %d",
                            channel.pk, channel.channel_type, attempt)
            return True, detail
        logger.warning("channel %s (%s) send failed (attempt %d/%d): %s",
                       channel.pk, channel.channel_type, attempt, attempts, detail)
        if attempt < attempts and backoff > 0:
            time.sleep(backoff * attempt)
    return False, last_detail


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
        # Re-read so the payload reflects the just-stamped resolved fields, etc.
        event.refresh_from_db()
        payload = build_payload(event, transition)
        if _suppressed_by_maintenance(payload):
            summary["reason"] = "maintenance"
            return summary
        channels = matching_channels(event, payload)
        summary["channels"] = len(channels)
        for ch in channels:
            ok, _detail = send_to_channel(ch, payload)
            summary["sent" if ok else "failed"] += 1
        summary["dispatched"] = True
        logger.info("dispatched alert %s [%s/%s]: %d channel(s), %d sent, %d failed",
                    event.pk, transition, payload.severity,
                    summary["channels"], summary["sent"], summary["failed"])
    except Exception as exc:  # noqa: BLE001 — dispatch must never crash the caller
        logger.warning("dispatch_event failed for event %s: %s", getattr(event, "pk", "?"), exc)
        summary["reason"] = "error"
    return summary
