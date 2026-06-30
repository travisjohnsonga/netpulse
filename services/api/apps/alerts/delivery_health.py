"""
Notification-delivery health — derived from NotificationLog so a silent dispatch
failure becomes visible. Read by the delivery-health API (UI status surface) and
by /api/health/infrastructure (external monitoring — "watch the watcher").

A channel is *unhealthy* when it has recent failures and no success since its last
failure (i.e. it's currently failing, not just flapped once and recovered).
"""
from __future__ import annotations

from datetime import timedelta


def delivery_health(window_minutes: int = 60) -> dict:
    from django.utils import timezone

    from .models import NotificationLog

    since = timezone.now() - timedelta(minutes=window_minutes)
    rows = NotificationLog.objects.filter(created_at__gte=since).values(
        "channel_id", "channel_name", "channel_type", "status", "created_at")

    by: dict = {}
    for r in rows:
        key = r["channel_id"] if r["channel_id"] is not None else f"type:{r['channel_type']}"
        d = by.setdefault(key, {
            "channel_id": r["channel_id"], "channel_name": r["channel_name"],
            "channel_type": r["channel_type"], "sent": 0, "failed": 0,
            "last_success": None, "last_failure": None,
        })
        ts = r["created_at"]
        if r["status"] == "sent":
            d["sent"] += 1
            if d["last_success"] is None or ts > d["last_success"]:
                d["last_success"] = ts
        else:
            d["failed"] += 1
            if d["last_failure"] is None or ts > d["last_failure"]:
                d["last_failure"] = ts

    channels, failing, total_failed = [], 0, 0
    for d in by.values():
        unhealthy = d["failed"] > 0 and (
            d["last_success"] is None
            or (d["last_failure"] is not None and d["last_success"] < d["last_failure"]))
        total_failed += d["failed"]
        if unhealthy:
            failing += 1
        channels.append({
            **d, "healthy": not unhealthy,
            "last_success": d["last_success"].isoformat() if d["last_success"] else None,
            "last_failure": d["last_failure"].isoformat() if d["last_failure"] else None,
        })
    # Unhealthy first, then by name, so the UI/banner leads with what's broken.
    channels.sort(key=lambda c: (c["healthy"], (c["channel_name"] or "")))
    return {
        "healthy": failing == 0,
        "channels_failing": failing,
        "recent_failures": total_failed,
        "window_minutes": window_minutes,
        "channels": channels,
    }
