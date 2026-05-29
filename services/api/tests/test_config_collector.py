import pytest
from django.core.management import call_command

from apps.compliance import collector
from apps.configbackup.models import DeviceConfig
from apps.credentials.models import CredentialProfile
from apps.devices.models import Device

pytestmark = pytest.mark.django_db


@pytest.fixture
def profile():
    return CredentialProfile.objects.create(
        name="ssh-prof", ssh_enabled=True, ssh_username="admin", vault_path="x")


@pytest.fixture
def device(profile):
    return Device.objects.create(
        hostname="rtr-01", ip_address="10.0.0.1", vendor="Cisco",
        platform="ios_xe", status="active", credential_profile=profile)


# ── Pure helpers ──────────────────────────────────────────────────────────────


class TestHelpers:
    @pytest.mark.parametrize("vendor,platform,expected", [
        ("Cisco", "ios", "cisco_ios"),
        ("Cisco", "ios_xe", "cisco_xe"),
        ("Cisco", "ios_xr", "cisco_xr"),
        ("Cisco", "nxos", "cisco_nxos"),
        ("Arista", "eos", "arista_eos"),
        ("Juniper", "junos", "juniper_junos"),
        ("", "sonic", "linux"),
        ("Arista", "other", "arista_eos"),   # vendor fallback
        ("Weird", "other", "linux"),         # final fallback
    ])
    def test_device_type_mapping(self, vendor, platform, expected):
        assert collector.netmiko_device_type(vendor, platform) == expected

    def test_config_command(self):
        assert collector.config_command("ios_xe") == "show running-config"
        assert collector.config_command("junos") == "show configuration"
        assert collector.config_command("eos") == "show running-config"


# ── collect_one ───────────────────────────────────────────────────────────────


class TestCollectOne:
    def test_first_collection_not_changed(self, device, monkeypatch):
        monkeypatch.setattr(collector, "_fetch_running_config", lambda d, c: "hostname rtr-01\n!")
        published = []
        monkeypatch.setattr(collector, "publish_collected", lambda did: published.append(did))

        cfg = collector.collect_one(device)
        assert cfg is not None
        assert cfg.config_type == "running"
        assert cfg.collected_by == "scheduled"
        assert cfg.changed_from_previous is False
        assert len(cfg.content_hash) == 64
        assert published == [device.id]
        device.refresh_from_db()
        assert device.last_seen is not None

    def test_change_detection_and_diff(self, device, monkeypatch):
        monkeypatch.setattr(collector, "publish_collected", lambda did: None)
        monkeypatch.setattr(collector, "_fetch_running_config", lambda d, c: "line a\nline b")
        collector.collect_one(device)
        # Same content again → not changed.
        c2 = collector.collect_one(device)
        assert c2.changed_from_previous is False
        # Different content → changed + diff captured.
        monkeypatch.setattr(collector, "_fetch_running_config", lambda d, c: "line a\nline c")
        c3 = collector.collect_one(device)
        assert c3.changed_from_previous is True
        assert c3.diff_summary and "line c" in c3.diff_summary
        assert DeviceConfig.objects.filter(device=device).count() == 3

    def test_timeout_does_not_crash(self, device, monkeypatch):
        def boom(d, c):
            raise TimeoutError("connect timed out")
        monkeypatch.setattr(collector, "_fetch_running_config", boom)
        assert collector.collect_one(device) is None
        assert DeviceConfig.objects.filter(device=device).count() == 0

    def test_auth_failure_marks_credential(self, device, monkeypatch):
        class NetmikoAuthenticationException(Exception):
            pass
        def boom(d, c):
            raise NetmikoAuthenticationException("bad creds")
        monkeypatch.setattr(collector, "_fetch_running_config", boom)
        assert collector.collect_one(device) is None
        device.credential_profile.refresh_from_db()
        assert device.credential_profile.last_test_result == "failure"

    def test_empty_config_skipped(self, device, monkeypatch):
        monkeypatch.setattr(collector, "_fetch_running_config", lambda d, c: "   ")
        assert collector.collect_one(device) is None
        assert DeviceConfig.objects.filter(device=device).count() == 0


# ── Management command ───────────────────────────────────────────────────────


class TestCommand:
    def test_once_collects_active_devices(self, device, monkeypatch):
        monkeypatch.setattr(collector, "_fetch_running_config", lambda d, c: "config!")
        monkeypatch.setattr(collector, "publish_collected", lambda did: None)
        # An inactive device should be skipped.
        Device.objects.create(hostname="down", ip_address="10.0.0.9", status="inactive")
        call_command("run_config_manager", "--once")
        assert DeviceConfig.objects.filter(device=device).count() == 1
        assert DeviceConfig.objects.filter(device__hostname="down").count() == 0

    def test_device_id_is_manual(self, device, monkeypatch):
        monkeypatch.setattr(collector, "_fetch_running_config", lambda d, c: "config!")
        monkeypatch.setattr(collector, "publish_collected", lambda did: None)
        call_command("run_config_manager", "--once", "--device-id", str(device.id))
        cfg = DeviceConfig.objects.get(device=device)
        assert cfg.collected_by == "manual"
