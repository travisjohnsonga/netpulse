"""
Alert auto-resolution: flip matching FIRING events to RESOLVED.

Used by the recovery paths (reachability monitor, check engine, interface
monitor) to close the alerts they previously opened, matched by their JSON
labels. Pure ORM + sync — wrap in sync_to_async from async callers.
"""
from __future__ import annotations


def resolve_matching(resolved_by: str = "auto", note: str = "", *, now=None, **label_filters) -> int:
    """
    Resolve all FIRING AlertEvents whose labels match the given filters.

    e.g. resolve_matching(note="reachable", source="reachability_monitor",
                          device_id=3) resolves firing reachability alerts for
    device 3. Returns the number of events resolved.
    """
    from django.utils import timezone

    from .models import AlertEvent

    if now is None:
        now = timezone.now()
    flt = {f"labels__{k}": v for k, v in label_filters.items()}
    return AlertEvent.objects.filter(state=AlertEvent.State.FIRING, **flt).update(
        state=AlertEvent.State.RESOLVED,
        resolved_at=now,
        resolved_by=resolved_by,
        resolution_note=note,
    )
