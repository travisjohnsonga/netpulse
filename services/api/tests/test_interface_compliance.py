"""Tests for the LLDP-aware interface compliance engine + API."""
import hashlib

import pytest
from django.utils import timezone

from apps.compliance import interface_compliance as ic
from apps.compliance.models import InterfaceComplianceResult, InterfaceComplianceRule
from apps.configbackup.models import DeviceConfig
from apps.devices.models import Device, DeviceRole, LLDPNeighbor

pytestmark = pytest.mark.django_db


def _config(device, content):
    return DeviceConfig.objects.create(
        device=device, collected_at=timezone.now(), content=content,
        content_hash=hashlib.sha256(content.encode()).hexdigest())


def _switch(host="sw1", ip="10.0.0.1", platform="aos_cx"):
    return Device.objects.create(hostname=host, ip_address=ip, platform=platform)


# AOS-CX-style config: 1/1/7 is an AP edge port (good), 1/1/8 is trunked (bad).
AOS_CONFIG = """\
vlan 10
vlan 20
interface 1/1/7
    no shutdown
    vlan access 10
    spanning-tree bpdu-guard
    no routing
interface 1/1/8
    no shutdown
    vlan trunk native 1
    vlan trunk allowed all
"""


class TestExtractBlock:
    def test_extracts_named_block(self):
        block = ic.extract_interface_block(AOS_CONFIG, "1/1/7")
        assert "vlan access 10" in block and "spanning-tree" in block
        assert "1/1/8" not in block  # stops at next interface

    def test_canonical_name_match(self):
        cfg = "interface GigabitEthernet1/0/5\n description AP-1\n spanning-tree portfast\n"
        # Abbreviated LLDP name resolves to the full header via canonical_ifname.
        assert "portfast" in ic.extract_interface_block(cfg, "Gi1/0/5")

    def test_missing_block_returns_empty(self):
        assert ic.extract_interface_block(AOS_CONFIG, "9/9/9") == ""


class TestChecks:
    def test_config_contains(self):
        assert ic.run_check({"type": "config_contains", "value": "spanning-tree"}, "x spanning-tree y")["passed"]
        assert not ic.run_check({"type": "config_contains", "value": "poe"}, "no match")["passed"]

    def test_config_not_contains(self):
        assert ic.run_check({"type": "config_not_contains", "value": "trunk"}, "vlan access 10")["passed"]
        assert not ic.run_check({"type": "config_not_contains", "value": "trunk"}, "vlan trunk native 1")["passed"]

    def test_vlan_check_access(self):
        assert ic.run_check({"type": "vlan_check", "vlan_type": "access"}, "vlan access 10")["passed"]
        assert not ic.run_check({"type": "vlan_check", "vlan_type": "access"}, "vlan trunk allowed all")["passed"]


class TestCapabilityTrigger:
    def _rule(self, **kw):
        defaults = dict(
            name="AP Ports", trigger="lldp_capability", trigger_value="wlan-access-point",
            platform="aos_cx",
            checks=[
                {"type": "config_contains", "value": "spanning-tree",
                 "description": "STP edge", "severity": "warning"},
                {"type": "config_not_contains", "value": "trunk",
                 "description": "no trunk", "severity": "error"},
            ])
        defaults.update(kw)
        return InterfaceComplianceRule.objects.create(**defaults)

    def test_normalizes_capability_and_matches(self):
        # trigger_value 'wlan-access-point' must match the stored canonical 'wlan-ap'.
        sw = _switch()
        _config(sw, AOS_CONFIG)
        LLDPNeighbor.objects.create(seen_by=sw, local_interface="1/1/7",
                                    system_name="ap-lobby", capabilities=["bridge", "wlan-ap"])
        out = ic.run_interface_compliance(self._rule())
        assert out["summary"] == {"matched": 1, "passing": 1, "failing": 0}
        assert out["results"][0]["neighbor"] == "ap-lobby"

    def test_failing_interface_reported(self):
        sw = _switch()
        _config(sw, AOS_CONFIG)
        LLDPNeighbor.objects.create(seen_by=sw, local_interface="1/1/8",
                                    system_name="ap-2", capabilities=["wlan-ap"])
        out = ic.run_interface_compliance(self._rule())
        assert out["summary"] == {"matched": 1, "passing": 0, "failing": 1}
        r = out["results"][0]
        assert not r["passed"]
        # spanning-tree missing AND trunk present → both checks fail.
        assert {f["value"] for f in r["findings"]} == {"spanning-tree", "trunk"}

    def test_platform_filter_excludes_other_switches(self):
        ios = _switch("ios-sw", "10.0.0.2", platform="ios")
        _config(ios, "interface 1/1/7\n spanning-tree\n")
        LLDPNeighbor.objects.create(seen_by=ios, local_interface="1/1/7",
                                    capabilities=["wlan-ap"])
        # rule platform=aos_cx → the ios switch is filtered out.
        assert ic.run_interface_compliance(self._rule())["summary"]["matched"] == 0

    def test_persists_results(self):
        sw = _switch()
        _config(sw, AOS_CONFIG)
        LLDPNeighbor.objects.create(seen_by=sw, local_interface="1/1/7", capabilities=["wlan-ap"])
        rule = self._rule()
        ic.run_interface_compliance(rule)
        assert InterfaceComplianceResult.objects.filter(rule=rule).count() == 1
        # Re-run replaces, doesn't duplicate.
        ic.run_interface_compliance(rule)
        assert InterfaceComplianceResult.objects.filter(rule=rule).count() == 1


class TestPlatformTrigger:
    def test_matches_by_neighbor_platform(self):
        sw = _switch()
        _config(sw, AOS_CONFIG)
        ap = Device.objects.create(hostname="mist-ap-1", ip_address="10.9.0.1", platform="mist_ap")
        LLDPNeighbor.objects.create(seen_by=sw, local_interface="1/1/7",
                                    system_name="mist-ap-1", matched_device=ap)
        rule = InterfaceComplianceRule.objects.create(
            name="P", trigger="lldp_neighbor_platform", trigger_value="unifi_ap,mist_ap",
            checks=[{"type": "config_contains", "value": "spanning-tree"}])
        assert ic.run_interface_compliance(rule)["summary"]["matched"] == 1


class TestDescriptionTrigger:
    def test_matches_interface_description_regex(self):
        sw = _switch()
        _config(sw, "interface 1/1/3\n description CAM-front-door\n vlan access 50\n")
        rule = InterfaceComplianceRule.objects.create(
            name="Cams", trigger="interface_description", trigger_value="(?i)(cam|camera)",
            checks=[{"type": "config_contains", "value": "access"}])
        out = ic.run_interface_compliance(rule)
        assert out["summary"]["matched"] == 1 and out["results"][0]["passed"]


class TestEndpoints:
    def test_crud_and_run(self, auth_client):
        sw = _switch()
        _config(sw, AOS_CONFIG)
        LLDPNeighbor.objects.create(seen_by=sw, local_interface="1/1/8", capabilities=["wlan-ap"])
        payload = {
            "name": "AP Ports", "trigger": "lldp_capability",
            "trigger_value": "wlan-access-point", "platform": "aos_cx", "enabled": True,
            "checks": [{"type": "config_not_contains", "value": "trunk",
                        "description": "no trunk", "severity": "error"}],
        }
        resp = auth_client.post("/api/compliance/interface-rules/", payload, format="json")
        assert resp.status_code == 201
        rid = resp.json()["id"]
        assert resp.json()["trigger_display"] == "LLDP Neighbor Capability"

        run = auth_client.post(f"/api/compliance/interface-rules/{rid}/run/", {}, format="json")
        assert run.status_code == 200 and run.json()["summary"]["failing"] == 1

        res = auth_client.get(f"/api/compliance/interface-results/?rule_id={rid}")
        rows = res.json()
        rows = rows["results"] if isinstance(rows, dict) else rows
        assert len(rows) == 1 and rows[0]["interface"] == "1/1/8" and rows[0]["passed"] is False

    def test_rejects_bad_checks(self, auth_client):
        resp = auth_client.post("/api/compliance/interface-rules/",
                                {"name": "x", "trigger_value": "wlan-ap", "checks": "nope"},
                                format="json")
        assert resp.status_code == 400


class TestSeeder:
    def test_seeds_disabled_rules_idempotently(self):
        from django.core.management import call_command
        DeviceRole.objects.get_or_create(slug="access-switch", defaults={"name": "Access Switch"})
        call_command("seed_compliance_rules")
        n = InterfaceComplianceRule.objects.count()
        assert n >= 7
        assert not InterfaceComplianceRule.objects.filter(enabled=True).exists()
        ap = InterfaceComplianceRule.objects.get(name="Wireless AP Port Config")
        assert ap.trigger == "lldp_capability" and ap.trigger_value == "wlan-access-point"
        call_command("seed_compliance_rules")  # idempotent
        assert InterfaceComplianceRule.objects.count() == n
