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


class TestFetchDevicesExclusions:
    """The monitor must not probe agent-linked or loopback/synthetic-IP devices —
    those produce permanent false 'unreachable' (agent hosts report their own
    liveness; a synthetic device IP points at the collector, not a real target)."""

    def test_excludes_agent_linked_device(self):
        from apps.devices.models import Device
        from apps.agents.models import Agent
        real = Device.objects.create(hostname="real", ip_address="10.0.0.1", status="active")
        agentdev = Device.objects.create(hostname="srv", ip_address="10.0.0.2", status="active")
        Agent.objects.create(hostname="srv", device=agentdev, status=Agent.Status.ACTIVE)
        ids = {d["id"] for d in _cmd()._fetch_devices()}
        assert real.id in ids
        assert agentdev.id not in ids

    def test_excludes_loopback_ip_device(self):
        from apps.devices.models import Device
        loop = Device.objects.create(hostname="loop", ip_address="127.0.0.1", status="unreachable")
        good = Device.objects.create(hostname="ok", ip_address="10.0.0.3", status="active")
        ids = {d["id"] for d in _cmd()._fetch_devices()}
        assert good.id in ids
        assert loop.id not in ids

    def test_management_ip_loopback_excluded_even_with_real_ip_address(self):
        from apps.devices.models import Device
        # _check probes management_ip first; a loopback mgmt IP must be excluded
        # regardless of a real ip_address (matches the probe-host precedence).
        d = Device.objects.create(hostname="m", ip_address="10.0.0.4",
                                  management_ip="127.0.0.1", status="active")
        ids = {x["id"] for x in _cmd()._fetch_devices()}
        assert d.id not in ids


class TestReachabilityApply:
    def test_reachable_updates_heartbeat(self, device):
        cmd = _cmd()
        t = cmd._apply_all([(_row(device), True, "tcp", 2.5)])
        device.refresh_from_db()
        assert device.is_reachable is True
        assert device.consecutive_failures == 0
        assert device.last_seen is not None and device.last_reachability_check is not None
        assert t == []  # no transition

    def test_failures_accumulate_then_unreachable(self, device):
        from apps.devices.models import Device
        cmd = _cmd()
        # 2 failures: still active, no transition
        cmd._apply_all([(_row(device), False, "tcp", None)])
        device.refresh_from_db(); assert device.consecutive_failures == 1 and device.status == "active"
        cmd._apply_all([(_row(device), False, "tcp", None)])
        device.refresh_from_db(); assert device.consecutive_failures == 2
        # 3rd failure → unreachable + high transition
        trans = cmd._apply_all([(_row(device), False, "tcp", None)])
        device.refresh_from_db()
        assert device.consecutive_failures == 3
        assert device.status == Device.Status.UNREACHABLE
        assert device.is_reachable is False
        assert device.unreachable_since is not None  # outage clock started
        assert trans and trans[0][0] == "high" and "unreachable" in trans[0][3]

    def test_recovery_flips_back_to_active(self, device):
        from apps.devices.models import Device
        device.status = Device.Status.UNREACHABLE
        device.consecutive_failures = 5
        device.is_reachable = False
        from django.utils import timezone
        device.unreachable_since = timezone.now()
        device.save()
        cmd = _cmd()
        trans = cmd._apply_all([(_row(device), True, "tcp", 2.5)])
        device.refresh_from_db()
        assert device.status == "active" and device.is_reachable is True
        assert device.consecutive_failures == 0
        assert device.unreachable_since is None  # outage clock cleared on recovery
        assert trans and trans[0][0] == "info" and "reachable again" in trans[0][3]


class TestFirstCycleBroadcast:
    def test_first_cycle_pushes_all_devices_then_stops(self, monkeypatch):
        # After a restart the first cycle must push every device's state (so
        # stale UIs refresh), even with no status transition; later cycles only
        # push real transitions. _apply_all/_fetch_devices are mocked so no DB
        # I/O crosses the asyncio boundary (which would taint the test
        # connection); the broadcast logic itself is what's under test.
        import asyncio
        cmd = _cmd()
        cmd._influx = None
        cmd._lat_state = {}
        cmd._first_cycle = True
        row = {"id": 7, "hostname": "r1", "management_ip": None,
               "ip_address": "10.0.0.1", "status": "active", "consecutive_failures": 0}
        pushes = []

        async def fake_push(payload): pushes.append(payload)
        async def fake_alert(*a, **k): pass
        async def fake_check(d, timeout): return (d, True, "tcp", 2.0)
        monkeypatch.setattr(cmd, "_push_ws", fake_push)
        monkeypatch.setattr(cmd, "_publish_alert", fake_alert)
        monkeypatch.setattr(cmd, "_check", fake_check)
        monkeypatch.setattr(cmd, "_fetch_devices", lambda: [row])
        monkeypatch.setattr(cmd, "_apply_all", lambda results: [])  # reachable, no transition

        asyncio.run(cmd._cycle(1.0))   # first cycle → broadcast even without transition
        assert len(pushes) == 1
        assert pushes[0]["device_id"] == 7 and pushes[0]["is_reachable"] is True

        pushes.clear()
        asyncio.run(cmd._cycle(1.0))   # steady state: no transition → no push
        assert pushes == []


class TestCheck:
    def test_falls_back_to_443_when_ssh_blocked(self, device, monkeypatch):
        import asyncio
        from apps.devices.management.commands import run_reachability_monitor as rm

        attempts = []

        class _W:
            def close(self): pass
            async def wait_closed(self): pass

        async def fake_open(host, port):
            attempts.append(port)
            if port == 22:
                raise OSError("connection refused")  # firewall blocks SSH
            return object(), _W()
        monkeypatch.setattr(rm.asyncio, "open_connection", fake_open)

        d, ok, method, rtt = asyncio.run(_cmd()._check(_row(device), 1.0))
        assert ok is True and method == "tcp/443" and attempts == [22, 443]
        assert isinstance(rtt, float)


class TestLatencyAlerts:
    def test_classify_latency(self):
        from apps.devices.management.commands.run_reachability_monitor import classify_latency
        assert classify_latency(None) == "ok"      # unreachable handled elsewhere
        assert classify_latency(5.0) == "ok"
        assert classify_latency(150.0) == "warn"    # > 100ms default
        assert classify_latency(600.0) == "crit"    # > 500ms default

    def test_warn_after_consecutive_then_recovers(self, device):
        cmd = _cmd(); cmd._lat_state = {}
        row = _row(device)
        assert cmd._latency_alerts([(row, True, "tcp", 150.0)]) == []   # 1/3
        assert cmd._latency_alerts([(row, True, "tcp", 150.0)]) == []   # 2/3
        a = cmd._latency_alerts([(row, True, "tcp", 150.0)])            # 3/3 → warn
        assert a and a[0][0] == "medium" and a[0][3] == "High Ping Latency"
        # Already warn → no re-emit while it stays high.
        assert cmd._latency_alerts([(row, True, "tcp", 150.0)]) == []
        # Drops back below threshold → info recovery.
        rec = cmd._latency_alerts([(row, True, "tcp", 5.0)])
        assert rec and rec[0][0] == "info"

    def test_crit_after_consecutive(self, device):
        cmd = _cmd(); cmd._lat_state = {}
        row = _row(device)
        assert cmd._latency_alerts([(row, True, "tcp", 600.0)]) == []   # 1/2
        a = cmd._latency_alerts([(row, True, "tcp", 600.0)])            # 2/2 → crit
        assert a and a[0][0] == "high" and a[0][3] == "Ping Latency Critical"

    def test_unreachable_resets_latency_state(self, device):
        cmd = _cmd(); cmd._lat_state = {}
        row = _row(device)
        cmd._latency_alerts([(row, True, "tcp", 150.0)])
        assert device.id in cmd._lat_state
        cmd._latency_alerts([(row, False, "tcp", None)])
        assert device.id not in cmd._lat_state


class TestReachabilitySerializer:
    def test_device_serializer_exposes_reachability(self, auth_client, device):
        body = auth_client.get(f"/api/devices/{device.id}/").json()
        assert "is_reachable" in body and "consecutive_failures" in body
        assert "last_reachability_check" in body

    def test_device_list_exposes_reachability(self, auth_client, device):
        row = auth_client.get("/api/devices/").json()["results"][0]
        assert "is_reachable" in row and "last_seen" in row


class TestDeviceStatusWebSocket:
    def test_consumer_receives_group_push(self):
        import asyncio
        from channels.testing import WebsocketCommunicator
        from channels.layers import get_channel_layer
        from apps.devices.consumers import DeviceStatusConsumer
        from tests.conftest import _make_user

        user = _make_user("ws_user", role="viewer")

        async def run():
            comm = WebsocketCommunicator(DeviceStatusConsumer.as_asgi(), "/ws/devices/")
            # The consumer now requires an authenticated user in the scope
            # (JWTAuthMiddleware sets this in production); inject one here.
            comm.scope["user"] = user
            connected, _ = await comm.connect()
            assert connected
            await get_channel_layer().group_send("devices", {
                "type": "device_status",
                "payload": {"device_id": 7, "hostname": "r7", "is_reachable": False, "status": "unreachable"},
            })
            msg = await comm.receive_json_from(timeout=2)
            assert msg["type"] == "device_status"
            assert msg["device_id"] == 7 and msg["is_reachable"] is False
            assert msg["status"] == "unreachable"
            await comm.disconnect()

        asyncio.run(run())

    def test_consumer_rejects_anonymous(self):
        import asyncio
        from channels.testing import WebsocketCommunicator
        from django.contrib.auth.models import AnonymousUser
        from apps.devices.consumers import DeviceStatusConsumer

        async def run():
            comm = WebsocketCommunicator(DeviceStatusConsumer.as_asgi(), "/ws/devices/")
            comm.scope["user"] = AnonymousUser()
            connected, code = await comm.connect()
            assert connected is False and code == 4401

        asyncio.run(run())

    def test_routing_includes_ws_devices(self):
        from apps.core.routing import websocket_urlpatterns
        assert any("ws/devices" in p.pattern.regex.pattern for p in websocket_urlpatterns)
