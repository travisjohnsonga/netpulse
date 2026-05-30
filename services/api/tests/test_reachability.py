import pytest

pytestmark = pytest.mark.django_db


def _cmd():
    from apps.devices.management.commands.run_reachability_monitor import Command
    return Command()


def _row(d):
    return {"id": d.id, "hostname": d.hostname, "management_ip": d.management_ip,
            "ip_address": d.ip_address, "status": d.status,
            "consecutive_failures": d.consecutive_failures}


@pytest.fixture
def device():
    from apps.devices.models import Device
    return Device.objects.create(hostname="r1", ip_address="10.0.0.1", status="active")


class TestReachabilityApply:
    def test_reachable_updates_heartbeat(self, device):
        cmd = _cmd()
        t = cmd._apply_all([(_row(device), True, "tcp")])
        device.refresh_from_db()
        assert device.is_reachable is True
        assert device.consecutive_failures == 0
        assert device.last_seen is not None and device.last_reachability_check is not None
        assert t == []  # no transition

    def test_failures_accumulate_then_unreachable(self, device):
        from apps.devices.models import Device
        cmd = _cmd()
        # 2 failures: still active, no transition
        cmd._apply_all([(_row(device), False, "tcp")])
        device.refresh_from_db(); assert device.consecutive_failures == 1 and device.status == "active"
        cmd._apply_all([(_row(device), False, "tcp")])
        device.refresh_from_db(); assert device.consecutive_failures == 2
        # 3rd failure → unreachable + high transition
        trans = cmd._apply_all([(_row(device), False, "tcp")])
        device.refresh_from_db()
        assert device.consecutive_failures == 3
        assert device.status == Device.Status.UNREACHABLE
        assert device.is_reachable is False
        assert trans and trans[0][0] == "high" and "unreachable" in trans[0][3]

    def test_recovery_flips_back_to_active(self, device):
        from apps.devices.models import Device
        device.status = Device.Status.UNREACHABLE
        device.consecutive_failures = 5
        device.is_reachable = False
        device.save()
        cmd = _cmd()
        trans = cmd._apply_all([(_row(device), True, "tcp")])
        device.refresh_from_db()
        assert device.status == "active" and device.is_reachable is True
        assert device.consecutive_failures == 0
        assert trans and trans[0][0] == "info" and "reachable again" in trans[0][3]


class TestReachabilitySerializer:
    def test_device_serializer_exposes_reachability(self, auth_client, device):
        body = auth_client.get(f"/api/devices/{device.id}/").json()
        assert "is_reachable" in body and "consecutive_failures" in body
        assert "last_reachability_check" in body

    def test_device_list_exposes_reachability(self, auth_client, device):
        row = auth_client.get("/api/devices/").json()["results"][0]
        assert "is_reachable" in row and "last_seen" in row
