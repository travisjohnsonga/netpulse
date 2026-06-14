"""
Config-collection health aggregation.

Derives fleet-wide collection health and the per-device failing list from
ConfigCollectionLog rows. Used by the ``collection-stats`` API endpoint and the
dashboard health widget so both report identical numbers.
"""
from __future__ import annotations

from datetime import timedelta

from django.db.models import Count
from django.utils import timezone

from apps.devices.models import Device

from .models import ConfigCollectionLog

_REACHED = set(ConfigCollectionLog.REACHED_STATUSES)


def _window_counts(since) -> dict:
    """Status counts over logs since ``since``, shaped for the UI summary."""
    rows = (
        ConfigCollectionLog.objects
        .filter(collected_at__gte=since)
        .values("status")
        .annotate(n=Count("id"))
    )
    by_status = {r["status"]: r["n"] for r in rows}
    S = ConfigCollectionLog.Status
    total = sum(by_status.values())
    reached = by_status.get(S.SUCCESS, 0) + by_status.get(S.UNCHANGED, 0)
    failed = total - reached
    return {
        "total": total,
        # "success" = reached (changed + unchanged); "unchanged" is a subset.
        "success": reached,
        "unchanged": by_status.get(S.UNCHANGED, 0),
        "failed": failed,
        "timeout": by_status.get(S.TIMEOUT, 0),
        "auth_failed": by_status.get(S.AUTH_FAILED, 0),
        "empty": by_status.get(S.EMPTY, 0),
        "success_rate": round(reached / total * 100, 1) if total else None,
    }


def failing_devices(limit: int = 50) -> list[dict]:
    """
    Devices whose most-recent collection attempt failed, newest-failure first.

    For each, report the last successful collection, how many consecutive
    attempts have failed since, and the last error.
    """
    out: list[dict] = []
    # Only consider devices that have at least one log; group the scan per device.
    device_ids = (
        ConfigCollectionLog.objects.values_list("device_id", flat=True).distinct()
    )
    devices = {d.id: d for d in Device.objects.filter(id__in=list(device_ids))}
    for did, device in devices.items():
        logs = list(
            ConfigCollectionLog.objects
            .filter(device_id=did)
            .order_by("-collected_at")
            .values("status", "collected_at", "error_message")[:50]
        )
        if not logs or logs[0]["status"] in _REACHED:
            continue  # currently healthy (or no logs)
        consecutive = 0
        last_success = None
        for log in logs:
            if log["status"] in _REACHED:
                last_success = log["collected_at"]
                break
            consecutive += 1
        out.append({
            "id": did,
            "hostname": device.hostname,
            "last_success": last_success,
            "consecutive_failures": consecutive,
            "last_error": logs[0]["status"],
        })
    out.sort(key=lambda r: r["consecutive_failures"], reverse=True)
    return out[:limit]


def collection_health() -> dict:
    """Full health payload for the stats endpoint + dashboard widget."""
    since = timezone.now() - timedelta(hours=24)
    collected_ids = set(
        ConfigCollectionLog.objects.values_list("device_id", flat=True).distinct()
    )
    never = (
        Device.objects.filter(status=Device.Status.ACTIVE)
        .exclude(id__in=collected_ids)
        .count()
    )
    return {
        "last_24h": _window_counts(since),
        "devices_never_collected": never,
        "devices_failing": failing_devices(),
    }
