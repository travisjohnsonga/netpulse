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
        ("Arista", "other", "arista_eos"),       # vendor fallback
        ("Weird", "other", "autodetect"),        # final fallback → autodetect
    ])
    def test_device_type_mapping(self, vendor, platform, expected):
        assert collector.netmiko_device_type(vendor, platform) == expected

    def test_config_command(self):
        assert collector.config_command("ios_xe") == "show running-config"
        assert collector.config_command("junos") == "show configuration | display set"
        assert collector.config_command("eos") == "show running-config"

    def test_device_host_prefers_management_ip(self, device):
        device.management_ip = "192.168.98.100"
        assert collector.device_host(device) == "192.168.98.100"
        device.management_ip = None
        assert collector.device_host(device) == device.ip_address


# ── collect_one ───────────────────────────────────────────────────────────────


class TestCollectOne:
    def test_first_collection_stores_baseline(self, device, monkeypatch):
        monkeypatch.setattr(collector, "_fetch_running_config", lambda d, c: "hostname rtr-01\n!")
        published = []
        monkeypatch.setattr(collector, "publish_collected", lambda did: published.append(did))

        res = collector.collect_one(device)
        assert res["ok"] is True and res["stored"] is True and res["changed"] is False
        cfg = res["config"]
        assert cfg.config_type == "running" and cfg.collected_by == "scheduled"
        assert cfg.changed_from_previous is False and len(cfg.content_hash) == 64
        assert published == [device.id]
        device.refresh_from_db()
        assert device.last_seen is not None

    def test_unchanged_not_stored_but_last_seen_updated(self, device, monkeypatch):
        monkeypatch.setattr(collector, "publish_collected", lambda did: None)
        monkeypatch.setattr(collector, "_fetch_running_config", lambda d, c: "line a\nline b")
        collector.collect_one(device)
        device.refresh_from_db(); first_seen = device.last_seen

        res = collector.collect_one(device)  # identical content
        assert res["ok"] is True and res["stored"] is False
        assert DeviceConfig.objects.filter(device=device).count() == 1
        device.refresh_from_db()
        assert device.last_seen >= first_seen  # last_seen refreshed regardless

    def test_change_detection_and_diff(self, device, monkeypatch):
        monkeypatch.setattr(collector, "publish_collected", lambda did: None)
        monkeypatch.setattr(collector, "_fetch_running_config", lambda d, c: "line a\nline b")
        collector.collect_one(device)
        monkeypatch.setattr(collector, "_fetch_running_config", lambda d, c: "line a\nline c")
        res = collector.collect_one(device)
        assert res["stored"] is True and res["changed"] is True
        assert res["config"].diff_summary and "line c" in res["config"].diff_summary
        assert DeviceConfig.objects.filter(device=device).count() == 2

    def test_normalization_ignores_dynamic_lines(self, device, monkeypatch):
        monkeypatch.setattr(collector, "publish_collected", lambda did: None)
        cfg1 = "! Last configuration change at 10:00:00 UTC Mon Jun 1 2026\nhostname rtr\n!"
        cfg2 = "! Last configuration change at 23:59:59 UTC Tue Jun 2 2026\nhostname rtr\n!"
        monkeypatch.setattr(collector, "_fetch_running_config", lambda d, c: cfg1)
        assert collector.collect_one(device)["stored"] is True
        # Only the timestamp line differs → normalized hash identical → not stored.
        monkeypatch.setattr(collector, "_fetch_running_config", lambda d, c: cfg2)
        res = collector.collect_one(device)
        assert res["stored"] is False
        assert DeviceConfig.objects.filter(device=device).count() == 1

    def test_timeout_does_not_crash(self, device, monkeypatch):
        def boom(d, c):
            raise TimeoutError("connect timed out")
        monkeypatch.setattr(collector, "_fetch_running_config", boom)
        res = collector.collect_one(device)
        assert res["ok"] is False and res["error"] == "timeout"
        assert DeviceConfig.objects.filter(device=device).count() == 0

    def test_auth_failure_marks_credential(self, device, monkeypatch):
        class NetmikoAuthenticationException(Exception):
            pass
        def boom(d, c):
            raise NetmikoAuthenticationException("bad creds")
        monkeypatch.setattr(collector, "_fetch_running_config", boom)
        res = collector.collect_one(device)
        assert res["ok"] is False and res["error"] == "auth_failed"
        device.credential_profile.refresh_from_db()
        assert device.credential_profile.last_test_result == "failure"

    def test_empty_config_skipped(self, device, monkeypatch):
        monkeypatch.setattr(collector, "_fetch_running_config", lambda d, c: "   ")
        res = collector.collect_one(device)
        assert res["ok"] is False and res["error"] == "empty"
        assert DeviceConfig.objects.filter(device=device).count() == 0

    def test_normalize_config_helper(self):
        raw = (
            "Building configuration...\n"
            "Current configuration : 1234 bytes\n"
            "! Last configuration change at 10:00:00\n"
            "hostname rtr\n"
            "! NVRAM config last updated at 09:00:00\n"
            "interface Gi0/0\n"
        )
        norm = collector.normalize_config(raw)
        assert "hostname rtr" in norm and "interface Gi0/0" in norm
        assert "Building configuration" not in norm
        assert "Current configuration" not in norm
        assert "Last configuration change" not in norm
        assert "NVRAM config last updated" not in norm


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
