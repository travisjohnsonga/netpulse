"""Devices covered by an active maintenance window report ``maintenance_until``
(list + detail) and are counted as maintenance, not up/down, in status-summary."""
from datetime import timedelta

import pytest
from django.utils import timezone

from apps.alerting.models import MaintenanceWindow
from apps.devices.models import Device, Site

pytestmark = pytest.mark.django_db


def _window(devices=(), sites=(), hours=4, start_offset_h=-1, **kw):
    now = timezone.now()
    w = MaintenanceWindow.objects.create(
        name="mw", start_time=now + timedelta(hours=start_offset_h),
        end_time=now + timedelta(hours=hours), **kw)
    w.devices.set(devices)
    w.sites.set(sites)
    return w


def _list_by_host(auth_client):
    rows = auth_client.get("/api/devices/").json()["results"]
    return {r["hostname"]: r["maintenance_until"] for r in rows}


@pytest.fixture
def aps():
    return [Device.objects.create(hostname=f"site1-ap-0{i}", ip_address=f"192.0.2.{40 + i}",
                                  status="unreachable", is_reachable=False) for i in (1, 2, 3)]


class TestMaintenanceUntil:
    def test_device_scoped_window(self, auth_client, aps):
        w = _window(devices=aps[:2])
        rows = _list_by_host(auth_client)
        assert rows["site1-ap-01"] and rows["site1-ap-02"]
        assert rows["site1-ap-03"] is None
        detail = auth_client.get(f"/api/devices/{aps[0].pk}/").json()
        assert detail["maintenance_until"] == w.end_time.isoformat()

    def test_site_scoped_window(self, auth_client, aps):
        s = Site.objects.create(name="Site 1")
        Device.objects.filter(pk=aps[2].pk).update(site=s)
        _window(sites=[s])
        rows = _list_by_host(auth_client)
        assert rows["site1-ap-03"] and rows["site1-ap-01"] is None

    def test_global_window_covers_all(self, auth_client, aps):
        _window()
        assert all(_list_by_host(auth_client).values())

    def test_latest_end_wins(self, auth_client, aps):
        _window(devices=[aps[0]], hours=2)
        long = _window(devices=[aps[0]], hours=8)
        detail = auth_client.get(f"/api/devices/{aps[0].pk}/").json()
        assert detail["maintenance_until"] == long.end_time.isoformat()

    @pytest.mark.parametrize("kw", [
        {"start_offset_h": 1},                    # not started yet
        {"hours": -0.5},                          # already ended
        {"is_active": False},                     # disabled
        {"check_types": ["http"]},                # service-check-only window
        {"severity_filter": ["critical"]},        # wouldn't mute device-down (high)
    ])
    def test_windows_that_dont_count(self, auth_client, aps, kw):
        _window(devices=aps, **kw)
        assert not any(_list_by_host(auth_client).values())

    def test_severity_filter_including_high_counts(self, auth_client, aps):
        _window(devices=[aps[0]], severity_filter=["high", "critical"])
        assert _list_by_host(auth_client)["site1-ap-01"]


class TestStatusSummaryMaintenance:
    def test_maintenance_devices_not_counted_down(self, auth_client, aps):
        Device.objects.create(hostname="site1-sw-01", ip_address="192.0.2.50",
                              status="active", is_reachable=True)
        _window(devices=aps[:2])
        body = auth_client.get("/api/devices/status-summary/").json()
        assert body == {"total": 4, "up": 1, "down": 1, "maintenance": 2}
