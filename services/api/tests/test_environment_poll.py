"""AOS-CX environment + PoE collection → InfluxDB (apps.telemetry.environment_poll).

InfluxDB writes are stubbed; the focus is the point schema (must match what
metrics_influx._environment_detail reads) and the standing PoE alert.
"""
import pytest
from django.utils import timezone

from apps.telemetry import environment_poll as ep
from apps.alerts.models import AlertEvent
from apps.devices.models import Device

pytestmark = pytest.mark.django_db


def _device(host="cx1", ip="10.5.0.1", with_creds=True):
    profile = None
    if with_creds:
        from apps.credentials.models import CredentialProfile
        profile = CredentialProfile.objects.create(
            name=f"cred-{host}", ssh_enabled=True, ssh_username="admin",
            vault_path=f"netpulse/credentials/{host}")
    return Device.objects.create(hostname=host, ip_address=ip, management_ip=ip,
                                 status=Device.Status.ACTIVE, platform="aos_cx",
                                 credential_profile=profile)


_ENV = {
    "temperatures": [
        {"name": "Temp-1/1", "temperature_c": 42.5, "status": "normal"},
        {"name": "Temp-bad", "temperature_c": 91.0, "status": "critical"},
    ],
    "fans": [{"name": "Fan-1", "rpm": 6800, "status": "ok"}],
    "power_supplies": [{"name": "PSU-1", "instantaneous_power": 95, "status": "ok"}],
}


class TestPoeSummary:
    def test_summary_and_pct(self):
        ports = [
            {"port": "1/1/1", "power_drawn": 20, "power_allocated": 30},
            {"port": "1/1/2", "power_drawn": 60, "power_allocated": 70},
        ]
        s = ep.poe_summary(ports)
        assert s["used_watts"] == 80 and s["budget_watts"] == 100
        assert s["used_pct"] == 80.0 and s["ports_delivering"] == 2

    def test_no_ports(self):
        assert ep.poe_summary([]) is None

    def test_zero_budget_omits_pct(self):
        s = ep.poe_summary([{"power_drawn": 0, "power_allocated": 0}])
        assert "used_pct" not in s


class TestPointSchema:
    def test_points_match_read_schema(self):
        dev = _device()
        env = {**_ENV, "poe": {"budget_watts": 100, "used_watts": 85, "used_pct": 85.0,
                               "status": "delivering"}}
        pts = ep._points_for(dev, env)
        # Decode points to {(sensor_type, sensor_name): {fields}}
        line = {p._name: p for p in pts}  # all device_environment
        assert all(p._name == "device_environment" for p in pts)
        by = {}
        for p in pts:
            tags = dict(p._tags)
            fields = dict(p._fields)
            by[(tags["sensor_type"], tags["sensor_name"])] = (tags, fields)
        # Temperature uses temperature_c + status_ok (read schema), tagged by device_id.
        t = by[("temperature", "Temp-1/1")]
        assert t[0]["device_id"] == str(dev.id)
        assert t[1]["temperature_c"] == 42.5 and t[1]["status_ok"] == 1
        assert by[("temperature", "Temp-bad")][1]["status_ok"] == 0   # critical → not ok
        # Fan / PSU / PoE field names match the reader.
        assert by[("fan", "Fan-1")][1]["fan_rpm"] == 6800.0
        assert by[("psu", "PSU-1")][1]["watts"] == 95.0
        poe = by[("poe", "poe")][1]
        assert poe["poe_used_pct"] == 85.0 and poe["poe_budget_watts"] == 100.0
        assert "device_environment" in line


class TestPoeAlert:
    def test_fires_and_resolves(self):
        dev = _device()
        ep.reconcile_poe_alert(dev, {"used_pct": 92.0, "used_watts": 92, "budget_watts": 100}, threshold=80)
        ev = AlertEvent.objects.filter(labels__alert_type="poe_usage", labels__device_id=dev.id)
        assert ev.filter(state=AlertEvent.State.FIRING).count() == 1
        # Re-running while still over does not create a second event.
        ep.reconcile_poe_alert(dev, {"used_pct": 90.0, "used_watts": 90, "budget_watts": 100}, threshold=80)
        assert ev.filter(state=AlertEvent.State.FIRING).count() == 1
        # Back under threshold resolves it.
        ep.reconcile_poe_alert(dev, {"used_pct": 50.0, "used_watts": 50, "budget_watts": 100}, threshold=80)
        assert ev.filter(state=AlertEvent.State.FIRING).count() == 0
        assert ev.filter(state=AlertEvent.State.RESOLVED).count() == 1

    def test_no_poe_no_alert(self):
        dev = _device()
        ep.reconcile_poe_alert(dev, None)
        assert not AlertEvent.objects.filter(labels__alert_type="poe_usage").exists()


class TestPollOrchestration:
    def test_polls_active_aoscx_and_writes(self, monkeypatch):
        dev = _device()
        Device.objects.create(hostname="ios1", ip_address="10.5.0.2",
                              status=Device.Status.ACTIVE, platform="ios")  # excluded
        env = {**_ENV, "poe": {"budget_watts": 100, "used_watts": 85, "used_pct": 85.0,
                               "status": "delivering"}}
        monkeypatch.setattr(ep, "collect_device_environment", lambda d: env)
        written = {}
        monkeypatch.setattr(ep, "_write_points", lambda pts: written.update(n=len(pts)))
        res = ep.poll_environments()
        assert res["devices"] == 1 and res["collected"] == 1
        assert written["n"] > 0
        # PoE > 80 → standing alert fired.
        assert AlertEvent.objects.filter(labels__alert_type="poe_usage",
                                         state=AlertEvent.State.FIRING).count() == 1

    def test_skips_device_without_credentials(self, monkeypatch):
        _device()
        monkeypatch.setattr(ep, "collect_device_environment", lambda d: None)
        monkeypatch.setattr(ep, "_write_points", lambda pts: None)
        res = ep.poll_environments()
        assert res["collected"] == 0
