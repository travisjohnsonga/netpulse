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


class MaintenanceLookup:
    """Per-request answer to "is this device in maintenance, and until when?".

    Loads the active windows once (one query + prefetch), so a device list page
    can stamp every row without a query per device. A window counts when it
    would suppress the device-down alert: no check-type narrowing, and a
    severity filter that is empty or includes "high". Global (unscoped) windows
    cover every device. Returns the latest end_time among covering windows.
    """

    def __init__(self, now=None):
        self.global_until = None
        self.by_device: dict = {}
        self.by_site: dict = {}
        for w in active_windows(now):
            if w.check_types:
                continue
            if w.severity_filter and "high" not in w.severity_filter:
                continue
            dev_ids = [d.id for d in w.devices.all()]
            site_ids = [s.id for s in w.sites.all()]
            if not dev_ids and not site_ids:
                self.global_until = _later(self.global_until, w.end_time)
            for i in dev_ids:
                self.by_device[i] = _later(self.by_device.get(i), w.end_time)
            for i in site_ids:
                self.by_site[i] = _later(self.by_site.get(i), w.end_time)

    def until(self, device_id, site_id=None):
        """End time of the covering maintenance window, or None."""
        until = self.global_until
        until = _later(until, self.by_device.get(device_id))
        if site_id:
            until = _later(until, self.by_site.get(site_id))
        return until


def _later(a, b):
    if a is None:
        return b
    if b is None:
        return a
    return max(a, b)
