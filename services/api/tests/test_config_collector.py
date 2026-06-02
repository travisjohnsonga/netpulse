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
        # FortiOS has no "show running-config".
        assert collector.config_command("fortios") == "show full-configuration"

    def test_normalize_strips_fortios_header(self):
        # FortiOS "show full-configuration" header drifts (version/conf-file-ver,
        # and #config-version embeds the username) without a real config change.
        a = (
            "#config-version=FGVM64-7.4.1-FW-build2463-230830:opmode=0:vdom=0:user=netpulse\n"
            "#conf_file_ver=100\n#buildno=2463\n#global_vdom=1\n"
            "config system global\n    set hostname \"fw1\"\nend\n"
        )
        b = (
            "#config-version=FGVM64-7.4.1-FW-build2463-230830:opmode=0:vdom=0:user=admin\n"
            "#conf_file_ver=215\n#buildno=2463\n#global_vdom=1\n"
            "config system global\n    set hostname \"fw1\"\nend\n"
        )
        # Different header (user + conf_file_ver) but identical substantive config.
        assert collector.normalize_config(a) == collector.normalize_config(b)

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

    def test_normalize_strips_vendor_dynamic_lines(self):
        # IOS-XR "!!", NX-OS "!Time", Juniper "## Last commit", NTP drift, EOS.
        raw = (
            "!! Last configuration change at Tue Jun 2 2026\n"
            "!Time: Mon Jun 1 10:00:00 2026\n"
            "## Last commit: 2026-06-01 10:00:00 UTC by admin\n"
            "ntp clock-period 17179847\n"
            "! Saved at 2026-06-01 10:00:00\n"
            "hostname rtr\n"
        )
        norm = collector.normalize_config(raw)
        assert "hostname rtr" in norm
        for dyn in ("Last configuration change", "!Time", "Last commit", "clock-period", "Saved at"):
            assert dyn not in norm

    def test_timestamp_drift_does_not_change_hash(self, device, monkeypatch):
        import apps.compliance.collector as c
        cfg1 = "!! Last configuration change at Mon Jun 1 2026\nntp clock-period 17179800\nhostname rtr\n"
        cfg2 = "!! Last configuration change at Tue Jun 2 2026\nntp clock-period 17179999\nhostname rtr\n"
        monkeypatch.setattr(c, "_fetch_running_config", lambda *a, **k: cfg1)
        first = c.store_config(device, cfg1, "test")
        assert first is not None
        # Only timestamp/drift lines differ → treated as unchanged.
        assert c.store_config(device, cfg2, "test") is None


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


class TestConfigTasks:
    def test_collect_device_config_delegates(self, device, monkeypatch):
        from apps.configbackup import tasks
        seen = {}
        monkeypatch.setattr(collector, "collect_one",
                            lambda d, collected_by="scheduled": seen.update(d=d.id, by=collected_by) or {"ok": True})
        res = tasks.collect_device_config(device.id, collected_by="enrichment")
        assert res["ok"] and seen == {"d": device.id, "by": "enrichment"}

    def test_collect_device_config_missing(self):
        from apps.configbackup import tasks
        assert tasks.collect_device_config(999999)["ok"] is False

    def test_collect_all_aggregates_and_alerts_on_change(self, device, monkeypatch):
        from apps.configbackup import tasks

        class Cfg:
            diff_summary = "+5 -2"
        monkeypatch.setattr(collector, "collect_one",
                            lambda d, collected_by="scheduled": {"ok": True, "stored": True, "changed": True, "config": Cfg()})
        alerts = []
        monkeypatch.setattr(tasks, "publish_config_change_alert", lambda dev, res: alerts.append(dev.id))
        out = tasks.collect_all_configs()
        assert out == {"total": 1, "success": 1, "failed": 0, "unchanged": 0, "changed": 1}
        assert alerts == [device.id]

    def test_collect_all_unchanged_no_alert(self, device, monkeypatch):
        from apps.configbackup import tasks
        monkeypatch.setattr(collector, "collect_one",
                            lambda d, collected_by="scheduled": {"ok": True, "stored": False, "changed": False, "config": None})
        alerts = []
        monkeypatch.setattr(tasks, "publish_config_change_alert", lambda dev, res: alerts.append(dev.id))
        out = tasks.collect_all_configs()
        assert out["unchanged"] == 1 and out["changed"] == 0 and alerts == []

    def test_publish_config_change_alert_payload(self, device, monkeypatch):
        from apps.configbackup import tasks
        captured = {}

        async def fake_publish(payload):
            captured.update(payload)
        monkeypatch.setattr(tasks, "_publish_alert", fake_publish)

        class Cfg:
            diff_summary = "+3 -1"
        tasks.publish_config_change_alert(device, {"config": Cfg()})
        assert captured["rule_name"] == "Config Changed"
        assert captured["device_id"] == device.id
        assert "+3 -1" in captured["message"]


class TestConfigSchedule:
    def test_collection_hours_default(self, monkeypatch):
        from apps.compliance.management.commands.run_config_manager import _collection_hours
        monkeypatch.delenv("CONFIG_COLLECTION_HOUR_1", raising=False)
        monkeypatch.delenv("CONFIG_COLLECTION_HOUR_2", raising=False)
        assert _collection_hours() == {7, 19}

    def test_collection_hours_override(self, monkeypatch):
        from apps.compliance.management.commands.run_config_manager import _collection_hours
        monkeypatch.setenv("CONFIG_COLLECTION_HOUR_1", "6")
        monkeypatch.setenv("CONFIG_COLLECTION_HOUR_2", "18")
        assert _collection_hours() == {6, 18}

    def test_enabled_flag(self, monkeypatch):
        from apps.compliance.management.commands.run_config_manager import _enabled
        monkeypatch.setenv("CONFIG_COLLECTION_ENABLED", "false")
        assert _enabled() is False
        monkeypatch.setenv("CONFIG_COLLECTION_ENABLED", "true")
        assert _enabled() is True
