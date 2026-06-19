"""
Running-vs-startup config mismatch detection: the check dispatch, persisting +
alerting in update_startup_match, the compliance-score component, and the
dashboard unsaved-configs count.
"""
import pytest
from django.utils import timezone

from apps.compliance import collector, device_score
from apps.configbackup.models import DeviceConfig
from apps.configbackup.stats import unsaved_config_devices
from apps.devices.models import Device

pytestmark = pytest.mark.django_db


@pytest.fixture
def device():
    return Device.objects.create(
        hostname="sw-startup", ip_address="10.8.0.1", management_ip="10.8.0.1", platform="aos_cx")


def _cfg(device, content="hostname sw-startup\n"):
    return DeviceConfig.objects.create(
        device=device, config_type=DeviceConfig.ConfigType.RUNNING,
        collected_at=timezone.now(), content=content, content_hash="y" * 8)


class TestCheckDispatch:
    def test_unsupported_platform(self, device):
        device.platform = "junos"
        res = collector.check_running_startup_match(device)
        assert res["checked"] is False
        assert res["match"] is None

    def test_aos_cx_match(self, device, monkeypatch):
        # Both configs fetched via SSH (CLI); identical → match.
        monkeypatch.setattr(collector, "_aos_cx_ssh_exec",
                            lambda d, p, c, cmd, **kw: "hostname sw\ninterface 1/1/1\n")
        from apps.credentials.models import CredentialProfile
        device.credential_profile = CredentialProfile.objects.create(name="p", ssh_username="admin")
        device.save()
        res = collector.check_running_startup_match(device)
        assert res["checked"] is True and res["match"] is True
        assert res["method"] == "ssh"

    def test_aos_cx_mismatch_produces_diff(self, device, monkeypatch):
        outputs = {
            "show running-config": "hostname sw\ninterface 1/1/1\n    no shutdown\n",
            "show startup-config": "hostname sw\ninterface 1/1/1\n",
        }
        monkeypatch.setattr(collector, "_aos_cx_ssh_exec",
                            lambda d, p, c, cmd, **kw: outputs[cmd])
        from apps.credentials.models import CredentialProfile
        device.credential_profile = CredentialProfile.objects.create(name="p", ssh_username="admin")
        device.save()
        res = collector.check_running_startup_match(device)
        assert res["checked"] is True and res["match"] is False
        assert res["added"] >= 1
        assert "running-config" in res["diff"]
        assert res["method"] == "ssh"


class TestUpdateAndAlert:
    def test_mismatch_stamps_config_and_fires_alert(self, device, monkeypatch):
        cfg = _cfg(device)
        monkeypatch.setattr(collector, "check_running_startup_match", lambda d: {
            "checked": True, "match": False, "diff": "+ vlan 55\n+   name New",
            "method": "rest", "added": 2, "removed": 0})
        collector.update_startup_match(device, cfg)
        cfg.refresh_from_db()
        assert cfg.startup_match is False
        assert "vlan 55" in cfg.startup_diff
        assert cfg.startup_checked_at is not None

        from apps.alerts.models import AlertEvent
        ev = AlertEvent.objects.get(labels__alert_type="config_unsaved", labels__device_id=device.id)
        assert ev.state == AlertEvent.State.FIRING
        assert "write memory" in ev.annotations["message"]

    def test_match_resolves_open_alert(self, device, monkeypatch):
        cfg = _cfg(device)
        monkeypatch.setattr(collector, "check_running_startup_match", lambda d: {
            "checked": True, "match": False, "diff": "+ x", "method": "rest", "added": 1, "removed": 0})
        collector.update_startup_match(device, cfg)
        # now configs match again
        monkeypatch.setattr(collector, "check_running_startup_match", lambda d: {
            "checked": True, "match": True, "diff": "", "method": "rest", "added": 0, "removed": 0})
        collector.update_startup_match(device, cfg)

        from apps.alerts.models import AlertEvent
        ev = AlertEvent.objects.get(labels__alert_type="config_unsaved", labels__device_id=device.id)
        assert ev.state == AlertEvent.State.RESOLVED
        cfg.refresh_from_db()
        assert cfg.startup_match is True

    def test_no_duplicate_alert_while_firing(self, device, monkeypatch):
        cfg = _cfg(device)
        monkeypatch.setattr(collector, "check_running_startup_match", lambda d: {
            "checked": True, "match": False, "diff": "+ x", "method": "rest", "added": 1, "removed": 0})
        collector.update_startup_match(device, cfg)
        collector.update_startup_match(device, cfg)
        from apps.alerts.models import AlertEvent
        assert AlertEvent.objects.filter(labels__alert_type="config_unsaved").count() == 1


class TestScoreAndStats:
    def test_startup_component_in_breakdown_and_penalises(self, device):
        cfg = _cfg(device)
        cfg.startup_match = False
        cfg.startup_diff = "+ vlan 55\n+ vlan 56"
        cfg.startup_checked_at = timezone.now()
        cfg.save()
        data = device_score.calculate_device_compliance_score(device)
        comp = next(b for b in data["breakdown"] if b["name"] == "Running/Startup Match")
        assert comp["score"] == 0.0
        assert comp["match"] is False
        assert data["score"] == 0.0          # only component → 0
        assert data["startup_status"]["match"] is False

    def test_unsaved_config_devices(self, device):
        cfg = _cfg(device)
        cfg.startup_match = False
        cfg.startup_checked_at = timezone.now()
        cfg.save()
        unsaved = unsaved_config_devices()
        assert any(d["hostname"] == "sw-startup" for d in unsaved)
