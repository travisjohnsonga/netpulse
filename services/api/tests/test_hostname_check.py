"""Tests for periodic/on-demand device hostname verification."""
import pytest

from apps.alerts.models import AlertEvent
from apps.devices import hostname_check
from apps.devices.models import Device

pytestmark = pytest.mark.django_db


def _device(hostname="sw-old", ip="10.20.0.1"):
    return Device.objects.create(hostname=hostname, ip_address=ip, management_ip=ip, status="active")


class TestCheckAndUpdateHostname:
    def test_updates_when_sysname_differs(self, monkeypatch):
        monkeypatch.setattr(hostname_check, "_snmp_sysname", lambda d: "sw-new")
        d = _device()
        res = hostname_check.check_and_update_hostname(d)
        assert res == {"hostname_changed": True, "old_hostname": "sw-old", "new_hostname": "sw-new"}
        d.refresh_from_db()
        assert d.hostname == "sw-new"
        assert d.hostname_verified_at is not None

    def test_records_info_alert_on_change(self, monkeypatch):
        monkeypatch.setattr(hostname_check, "_snmp_sysname", lambda d: "sw-new")
        d = _device()
        hostname_check.check_and_update_hostname(d)
        ev = AlertEvent.objects.filter(rule__name=hostname_check.HOSTNAME_CHANGE_RULE_NAME).first()
        assert ev is not None
        assert ev.rule.severity == "info"
        assert ev.annotations["old_hostname"] == "sw-old" and ev.annotations["new_hostname"] == "sw-new"
        assert '"sw-old"' in ev.annotations["message"] and '"sw-new"' in ev.annotations["message"]

    def test_unchanged_stamps_verified_no_alert(self, monkeypatch):
        monkeypatch.setattr(hostname_check, "_snmp_sysname", lambda d: "sw-old")
        d = _device()
        res = hostname_check.check_and_update_hostname(d)
        assert res["hostname_changed"] is False
        d.refresh_from_db()
        assert d.hostname == "sw-old" and d.hostname_verified_at is not None
        assert not AlertEvent.objects.filter(rule__name=hostname_check.HOSTNAME_CHANGE_RULE_NAME).exists()

    def test_dns_fallback_used_when_snmp_empty(self, monkeypatch):
        monkeypatch.setattr(hostname_check, "_snmp_sysname", lambda d: "")
        monkeypatch.setattr(hostname_check, "_dns_reverse", lambda ip: "sw-dns")
        d = _device()
        res = hostname_check.check_and_update_hostname(d)
        assert res["hostname_changed"] is True and res["new_hostname"] == "sw-dns"

    def test_no_value_leaves_hostname(self, monkeypatch):
        monkeypatch.setattr(hostname_check, "_snmp_sysname", lambda d: "")
        monkeypatch.setattr(hostname_check, "_dns_reverse", lambda ip: "")
        d = _device()
        res = hostname_check.check_and_update_hostname(d)
        assert res["hostname_changed"] is False
        d.refresh_from_db()
        assert d.hostname == "sw-old" and d.hostname_verified_at is not None

    def test_collision_guard(self, monkeypatch):
        # The detected name already belongs to another device → must not steal it.
        _device(hostname="sw-taken", ip="10.20.0.9")
        monkeypatch.setattr(hostname_check, "_snmp_sysname", lambda d: "sw-taken")
        d = _device()
        res = hostname_check.check_and_update_hostname(d)
        assert res["hostname_changed"] is False
        d.refresh_from_db()
        assert d.hostname == "sw-old"

    def test_reapplies_hostname_rules_on_change(self, monkeypatch):
        from apps.devices.models import DeviceRole, HostnameRule
        role = DeviceRole.objects.create(name="Firewall", slug="firewall")
        HostnameRule.objects.create(name="fw", pattern=r"^fw-", rule_type="role", role=role, enabled=True)
        monkeypatch.setattr(hostname_check, "_snmp_sysname", lambda d: "fw-1")
        d = _device()
        hostname_check.check_and_update_hostname(d)
        d.refresh_from_db()
        assert d.role_id == role.id


class TestCheckAllHostnames:
    def test_iterates_active_devices(self, monkeypatch):
        monkeypatch.setattr(hostname_check, "_snmp_sysname", lambda d: d.hostname + "-x")
        _device(hostname="a", ip="10.21.0.1")
        _device(hostname="b", ip="10.21.0.2")
        Device.objects.create(hostname="inactive", ip_address="10.21.0.3", status="inactive")
        res = hostname_check.check_all_hostnames()
        assert res == {"checked": 2, "changed": 2}  # inactive skipped


class TestCheckHostnameEndpoint:
    def test_endpoint_returns_change(self, auth_client, monkeypatch):
        monkeypatch.setattr(hostname_check, "_snmp_sysname", lambda d: "sw-new")
        d = _device()
        resp = auth_client.post(f"/api/devices/{d.id}/check-hostname/")
        assert resp.status_code == 200
        assert resp.json() == {"hostname_changed": True, "old_hostname": "sw-old", "new_hostname": "sw-new"}
