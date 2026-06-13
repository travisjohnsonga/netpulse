"""Tests for the cross-device (role) consistency engine + API."""
import hashlib

import pytest
from django.utils import timezone

from apps.compliance import role_consistency as rc
from apps.compliance.models import RoleConsistencyRule
from apps.configbackup.models import DeviceConfig
from apps.devices.models import Device, DeviceRole

pytestmark = pytest.mark.django_db


def _config(device, content):
    return DeviceConfig.objects.create(
        device=device, collected_at=timezone.now(), content=content,
        content_hash=hashlib.sha256(content.encode()).hexdigest())


@pytest.fixture
def access_role(db):
    return DeviceRole.objects.create(slug="access-switch", name="Access Switch")


def _sw(host, ip, role, vlans, platform="aos_cx"):
    d = Device.objects.create(hostname=host, ip_address=ip, platform=platform, role=role)
    _config(d, "".join(f"vlan {v}\n" for v in vlans))
    return d


class TestVlanParsing:
    def test_expand_vlan_range(self):
        assert rc.expand_vlan_range("1,10,20-23") == {1, 10, 20, 21, 22, 23}
        assert rc.expand_vlan_range("bad,5") == {5}

    def test_parse_aoscx(self):
        assert rc.parse_vlans_from_config("vlan 10\nvlan 20,30\n", "aos_cx") == {10, 20, 30}

    def test_get_device_vlans_falls_back_to_config(self):
        d = _sw("s", "10.1.0.9", None, [10, 20])
        # platform aos_cx but no credential_profile → REST skipped, config parsed.
        assert rc.get_device_vlans(d) == {10, 20}


class TestConsistency:
    def test_majority_vote_flags_drift(self, access_role):
        _sw("sw1", "10.1.0.1", access_role, [1, 10, 20, 30])
        _sw("sw2", "10.1.0.2", access_role, [1, 10, 20, 30])
        _sw("sw3", "10.1.0.3", access_role, [1, 10, 20])  # missing 30
        rule = RoleConsistencyRule.objects.create(
            name="VLANs", check_type="vlan_consistency", role=access_role,
            platform="aos_cx", excluded_vlans=[1])
        out = rc.run_role_consistency(rule)
        assert out["status"] == "complete"
        assert out["expected"] == [10, 20, 30]  # 30 on 2/3 majority; 1 excluded
        assert out["passing"] == 2 and out["failing"] == 1
        fail = next(r for r in out["results"] if r["status"] == "fail")
        assert fail["device"] == "sw3" and fail["missing"] == [30]
        assert "vlan 30" in fail["remediation"]

    def test_extra_vlan_flagged(self, access_role):
        _sw("a", "10.2.0.1", access_role, [10, 20])
        _sw("b", "10.2.0.2", access_role, [10, 20])
        _sw("c", "10.2.0.3", access_role, [10, 20, 999])  # extra 999
        rule = RoleConsistencyRule.objects.create(
            name="VLANs2", check_type="vlan_consistency", role=access_role)
        out = rc.run_role_consistency(rule)
        fail = next(r for r in out["results"] if r["status"] == "fail")
        assert fail["device"] == "c" and fail["extra"] == [999]
        assert "no vlan 999" in fail["remediation"]

    def test_skip_when_fewer_than_two(self, access_role):
        _sw("solo", "10.3.0.1", access_role, [10])
        rule = RoleConsistencyRule.objects.create(name="solo", role=access_role)
        out = rc.run_role_consistency(rule)
        assert out["status"] == "skip"

    def test_persists_last_summary(self, access_role):
        _sw("p1", "10.4.0.1", access_role, [10, 20])
        _sw("p2", "10.4.0.2", access_role, [10, 20])
        rule = RoleConsistencyRule.objects.create(name="p", role=access_role)
        rc.run_role_consistency(rule)
        rule.refresh_from_db()
        assert rule.last_run is not None and rule.last_summary["status"] == "complete"


class TestEndpoints:
    def test_crud_and_run(self, auth_client, access_role):
        _sw("e1", "10.5.0.1", access_role, [10, 20, 30])
        _sw("e2", "10.5.0.2", access_role, [10, 20, 30])
        _sw("e3", "10.5.0.3", access_role, [10, 20])  # missing 30
        payload = {"name": "Access VLANs", "check_type": "vlan_consistency",
                   "role": access_role.id, "platform": "aos_cx",
                   "excluded_vlans": [1], "severity": "warning", "enabled": True}
        resp = auth_client.post("/api/compliance/role-rules/", payload, format="json")
        assert resp.status_code == 201
        rid = resp.json()["id"]
        assert resp.json()["check_type_display"] == "VLAN Consistency"
        run = auth_client.post(f"/api/compliance/role-rules/{rid}/run/", {}, format="json")
        body = run.json()
        assert run.status_code == 200 and body["failing"] == 1 and body["expected"] == [10, 20, 30]

    def test_seeded_role_rules(self, auth_client, access_role):
        from django.core.management import call_command
        call_command("seed_compliance_rules")
        names = set(RoleConsistencyRule.objects.values_list("name", flat=True))
        assert "Access Switch VLAN Consistency" in names
        assert not RoleConsistencyRule.objects.filter(enabled=True).exists()
        # The access-switch rule linked to the existing role.
        r = RoleConsistencyRule.objects.get(name="Access Switch VLAN Consistency")
        assert r.role_id == access_role.id and r.excluded_vlans == [1]
