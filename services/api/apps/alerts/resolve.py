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
    qs = AlertEvent.objects.filter(state=AlertEvent.State.FIRING, **flt)
    # Capture the pks before the bulk update so we can dispatch recovery
    # notifications — a .update() bypasses the post_save signal that drives
    # dispatch for save()-based resolutions.
    pks = list(qs.values_list("pk", flat=True))
    resolved = qs.update(
        state=AlertEvent.State.RESOLVED,
        resolved_at=now,
        resolved_by=resolved_by,
        resolution_note=note,
    )
    if pks:
        notify_resolved(pks)
    return resolved


def notify_resolved(pks) -> None:
    """Dispatch the RESOLVED transition for already-resolved events by pk.

    Used by the ``.update()``-based resolution paths (this module + the
    bulk-resolve view) that bypass the post_save signal. Deferred to
    ``transaction.on_commit`` and fully best-effort; dispatch itself is
    idempotent so overlapping with the signal path is safe.
    """
    from django.db import transaction

    def _run():
        from .dispatch import dispatch_event
        from .models import AlertEvent
        for ev in AlertEvent.objects.filter(pk__in=list(pks)).select_related("rule"):
            try:
                dispatch_event(ev, "resolved")
            except Exception:  # noqa: BLE001 — never raise from a resolve path
                pass

    transaction.on_commit(_run)
