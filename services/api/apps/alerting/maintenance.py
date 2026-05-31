"""
Maintenance-window suppression.

is_in_maintenance() answers "should an alert for this device/severity/check-type
be suppressed right now?" — checked by the recovery/alert paths before they
publish or notify. An empty-scope window (no devices, no sites) suppresses all.
"""
from __future__ import annotations


def active_windows(now=None):
    from django.utils import timezone
    from .models import MaintenanceWindow
    now = now or timezone.now()
    return (MaintenanceWindow.objects
            .filter(is_active=True, start_time__lte=now, end_time__gte=now)
            .prefetch_related("devices", "sites"))


def is_in_maintenance(device_id=None, severity=None, check_type=None, now=None) -> bool:
    """True if an active maintenance window covers this alert."""
    site_id = None
    if device_id:
        from apps.devices.models import Device
        site_id = Device.objects.filter(id=device_id).values_list("site_id", flat=True).first()

    for w in active_windows(now):
        # Per-window severity / check-type narrowing (empty = applies to all).
        if w.severity_filter and severity and severity not in w.severity_filter:
            continue
        if w.check_types and check_type and check_type not in w.check_types:
            continue
        dev_ids = set(w.devices.values_list("id", flat=True))
        site_ids = set(w.sites.values_list("id", flat=True))
        if not dev_ids and not site_ids:
            return True  # global window — suppress everything
        if device_id and device_id in dev_ids:
            return True
        if site_id and site_id in site_ids:
            return True
    return False
