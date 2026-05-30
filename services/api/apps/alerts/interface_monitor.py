"""
Interface state-change alerting.

When a MonitoredInterface transitions up→down or down→up, raise an AlertEvent.
Events hang off a single system AlertRule ("Interface State Change"); per-event
severity, device, interface and human-readable title/message live in the event
labels/annotations (the schema keeps severity on the rule, so per-interface
severity is carried on the event instead).

This is invoked by the poller/stream-processor as interface status is observed.
``process_interface_status`` is pure enough to unit-test without a device.
"""
from __future__ import annotations

import logging

from django.utils import timezone

logger = logging.getLogger(__name__)

SYSTEM_RULE_NAME = "Interface State Change"
SOURCE = "interface_monitor"


def _system_rule():
    from .models import AlertRule
    rule, _ = AlertRule.objects.get_or_create(
        name=SYSTEM_RULE_NAME,
        defaults={
            "description": "Auto-generated alerts for monitored interface up/down transitions.",
            "severity": AlertRule.Severity.HIGH,
            "condition": {"rule_type": "interface_state_change"},
            "cooldown_minutes": 0,
        },
    )
    return rule


def _fmt_duration(seconds: float) -> str:
    seconds = int(max(0, seconds))
    if seconds < 60:
        return f"{seconds}s"
    m, s = divmod(seconds, 60)
    if m < 60:
        return f"{m}m {s}s"
    h, m = divmod(m, 60)
    if h < 24:
        return f"{h}h {m}m"
    d, h = divmod(h, 24)
    return f"{d}d {h}h"


def process_interface_status(iface, new_status: str, now=None) -> "object | None":
    """
    Compare ``new_status`` against the interface's stored state and, on a
    transition, persist the new state and raise an AlertEvent (subject to the
    interface's alert toggles). Returns the created AlertEvent or None.

    ``new_status`` is normalised to "up"/"down"; anything else is treated as a
    non-actionable status and only updates last_status.
    """
    from .models import AlertEvent

    now = now or timezone.now()
    prev = (iface.last_status or "unknown").lower()
    cur = (new_status or "unknown").lower()
    if cur not in ("up", "down"):
        iface.last_status = cur
        iface.save(update_fields=["last_status"])
        return None
    if cur == prev:
        return None  # no transition

    # Compute downtime when recovering (before we overwrite last_status_changed).
    downtime = None
    if cur == "up" and prev == "down" and iface.last_status_changed:
        downtime = (now - iface.last_status_changed).total_seconds()

    iface.last_status = cur
    iface.last_status_changed = now
    iface.save(update_fields=["last_status", "last_status_changed"])

    if cur == "down" and not iface.alert_on_down:
        return None
    if cur == "up" and not iface.alert_on_up:
        return None
    if prev == "unknown":
        return None  # first observation establishes a baseline, no alert

    device = iface.device
    ts = now.strftime("%H:%M:%S UTC")
    if cur == "down":
        severity = iface.alert_severity
        title = f"Interface Down: {device.hostname} {iface.if_name}"
        message = f"{iface.if_name} on {device.hostname} changed from up to down at {ts}"
    else:
        severity = "info"
        recovered = f" after being down for {_fmt_duration(downtime)}" if downtime is not None else ""
        title = f"Interface Recovered: {device.hostname} {iface.if_name}"
        message = f"{iface.if_name} on {device.hostname} is back up{recovered}"

    event = AlertEvent.objects.create(
        rule=_system_rule(),
        state=AlertEvent.State.FIRING,
        labels={
            "source": SOURCE,
            "device": device.hostname,
            "device_id": device.id,
            "interface": iface.if_name,
            "severity": severity,
            "transition": cur,
        },
        annotations={
            "title": title,
            "message": message,
            "severity": severity,
            "downtime_seconds": int(downtime) if downtime is not None else None,
        },
    )
    logger.info("interface alert: %s", title)
    return event
