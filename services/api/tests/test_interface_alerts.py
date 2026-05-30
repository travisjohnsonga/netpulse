import pytest
from django.utils import timezone

from apps.alerts import interface_monitor
from apps.alerts.models import AlertEvent

pytestmark = pytest.mark.django_db


@pytest.fixture
def iface(db):
    from apps.devices.models import Device
    from apps.telemetry.models import MonitoredInterface
    d = Device.objects.create(hostname="router1", ip_address="10.9.0.1", platform="ios_xe", status="active")
    return MonitoredInterface.objects.create(device=d, if_name="GigabitEthernet4", last_status="up")


class TestProcessStatus:
    def test_up_to_down_raises_alert(self, iface):
        ev = interface_monitor.process_interface_status(iface, "down")
        assert ev is not None
        assert ev.labels["transition"] == "down" and ev.labels["severity"] == "high"
        assert "Interface Down" in ev.annotations["title"]
        iface.refresh_from_db()
        assert iface.last_status == "down" and iface.last_status_changed is not None

    def test_recovery_reports_downtime(self, iface):
        down_at = timezone.now() - timezone.timedelta(minutes=4, seconds=32)
        iface.last_status = "down"; iface.last_status_changed = down_at; iface.save()
        ev = interface_monitor.process_interface_status(iface, "up")
        assert ev.labels["severity"] == "info"
        assert "Recovered" in ev.annotations["title"]
        assert ev.annotations["downtime_seconds"] >= 270

    def test_no_alert_when_no_transition(self, iface):
        assert interface_monitor.process_interface_status(iface, "up") is None

    def test_down_suppressed_when_alert_off(self, iface):
        iface.alert_on_down = False; iface.save()
        assert interface_monitor.process_interface_status(iface, "down") is None
        # state still updated even when not alerting
        iface.refresh_from_db(); assert iface.last_status == "down"

    def test_recovery_suppressed_when_alert_off(self, iface):
        iface.last_status = "down"; iface.alert_on_up = False; iface.save()
        assert interface_monitor.process_interface_status(iface, "up") is None

    def test_first_observation_no_alert(self, db):
        from apps.devices.models import Device
        from apps.telemetry.models import MonitoredInterface
        d = Device.objects.create(hostname="r2", ip_address="10.9.0.2", status="active")
        i = MonitoredInterface.objects.create(device=d, if_name="Gi1", last_status="unknown")
        assert interface_monitor.process_interface_status(i, "down") is None
        assert AlertEvent.objects.count() == 0


class TestAlertConfigEndpoint:
    def test_bulk_apply(self, auth_client, iface):
        from apps.telemetry.models import MonitoredInterface
        MonitoredInterface.objects.create(device=iface.device, if_name="Gi9", last_status="up")
        resp = auth_client.post(f"/api/devices/{iface.device_id}/interfaces/alert-config/", {
            "if_names": ["GigabitEthernet4", "Gi9"],
            "alert_on_down": False, "alert_severity": "critical",
        }, format="json")
        assert resp.status_code == 200
        for i in MonitoredInterface.objects.filter(device=iface.device):
            assert i.alert_on_down is False and i.alert_severity == "critical"

    def test_serializer_exposes_alert_fields(self, auth_client, iface):
        body = auth_client.get(f"/api/devices/{iface.device_id}/interfaces/").json()
        assert body[0]["alert_on_down"] is True and body[0]["alert_severity"] == "high"
        assert body[0]["consecutive_polls_before_alert"] == 1
