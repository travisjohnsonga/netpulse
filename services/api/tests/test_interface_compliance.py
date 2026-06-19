"""Tests for the LLDP-aware interface compliance engine + API."""
import hashlib
import json

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


# AOS-CX REST running-config JSON (real shape: split Interface/Port sections,
# URL-encoded keys, string-boolean stp_config). 1/1/14 = native-untagged trunk
# AP port; 1/1/20 = access; 1/1/30 = admin-down access (no Interface entry).
AOS_JSON = json.dumps({
    "Interface": {
        "1%2F1%2F14": {"description": "Standard Access Port for PC & Phone", "name": "1/1/14"},
        "1%2F1%2F20": {"description": "Uplink", "name": "1/1/20"},
    },
    "Port": {
        "1%2F1%2F14": {
            "name": "1/1/14", "vlan_mode": "native-untagged", "vlan_tag": "600",
            "vlan_trunks": ["600", "602", "10"],
            "stp_config": {"admin_edge_port_enable": "true", "bpdu_guard_enable": "true"},
            "loop_protect_enable": True,
        },
        "1%2F1%2F20": {"name": "1/1/20", "vlan_mode": "access", "vlan_tag": "10", "stp_config": {}},
        "1%2F1%2F30": {"name": "1/1/30", "vlan_mode": "access", "vlan_tag": "5", "admin": "down"},
    },
})


class TestAosCxJsonConfig:
    def test_json_interface_rendered_as_pseudo_cli(self):
        sw = _switch()
        _config(sw, AOS_JSON)
        block = ic.get_interface_config(sw, "1/1/14")
        assert block.startswith("interface 1/1/14")
        assert "description Standard Access Port for PC & Phone" in block
        assert "no shutdown" in block
        assert "vlan trunk native 600" in block
        assert "vlan trunk allowed 600,602,10" in block
        assert "spanning-tree bpdu-guard" in block
        assert "spanning-tree port-type admin-edge" in block
        assert "loop-protect" in block

    def test_json_access_port_has_access_not_trunk(self):
        sw = _switch()
        _config(sw, AOS_JSON)
        block = ic.get_interface_config(sw, "1/1/20")
        assert "vlan access 10" in block
        assert "trunk" not in block  # vlan_check 'access' requires no 'trunk'
        assert "description Uplink" in block

    def test_json_admin_down_renders_shutdown(self):
        sw = _switch()
        _config(sw, AOS_JSON)
        block = ic.get_interface_config(sw, "1/1/30")
        assert "shutdown" in block and "no shutdown" not in block

    def test_json_missing_interface_returns_empty(self):
        sw = _switch()
        _config(sw, AOS_JSON)
        assert ic.get_interface_config(sw, "9/9/9") == ""

    def test_cli_config_still_extracted(self):
        sw = _switch()
        _config(sw, AOS_CONFIG)
        block = ic.get_interface_config(sw, "1/1/7")
        assert "vlan access 10" in block and "spanning-tree bpdu-guard" in block

    def test_vlan_check_end_to_end_against_json(self):
        sw = _switch()
        _config(sw, AOS_JSON)
        block = ic.get_interface_config(sw, "1/1/20")
        assert ic.run_check({"type": "vlan_check", "vlan_type": "access"}, block)["passed"] is True
        assert ic.run_check({"type": "vlan_check", "vlan_type": "trunk"}, block)["passed"] is False


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

    def test_matches_raw_unnormalized_wlan(self):
        # A neighbour record collected before the normaliser learned "wlan"
        # (stored raw as ['bridge', 'wlan']) must still match a wlan-access-point
        # rule — the engine normalises both sides.
        sw = _switch()
        _config(sw, AOS_CONFIG)
        LLDPNeighbor.objects.create(seen_by=sw, local_interface="1/1/7",
                                    system_name="wco2-wh-ap-05", capabilities=["bridge", "wlan"])
        out = ic.run_interface_compliance(self._rule())
        assert out["summary"]["matched"] == 1 and out["results"][0]["passed"]

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

    def test_compound_capability_fields_persist(self, auth_client):
        # The serializer must expose + persist require/exclude (was dropping them).
        payload = {
            "name": "Uplinks", "trigger": "lldp_capability", "trigger_value": "bridge",
            "trigger_require_capabilities": ["router"],
            "trigger_exclude_capabilities": ["wlan-ap"],
            "platform": "", "enabled": True, "checks": [],
        }
        resp = auth_client.post("/api/compliance/interface-rules/", payload, format="json")
        assert resp.status_code == 201
        body = resp.json()
        assert body["trigger_require_capabilities"] == ["router"]
        assert body["trigger_exclude_capabilities"] == ["wlan-ap"]
        rule = InterfaceComplianceRule.objects.get(id=body["id"])
        assert rule.trigger_require_capabilities == ["router"]
        assert rule.trigger_exclude_capabilities == ["wlan-ap"]

    def test_require_capabilities_rejects_non_list(self, auth_client):
        resp = auth_client.post(
            "/api/compliance/interface-rules/",
            {"name": "x", "trigger_value": "bridge",
             "trigger_require_capabilities": "router", "checks": []},
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


class TestCompoundCapabilityTrigger:
    """require/exclude disambiguate shared capabilities (APs and switches both
    advertise 'bridge')."""

    def _rule(self, **kw):
        defaults = dict(name="r", trigger="lldp_capability", trigger_value="bridge",
                        platform="", checks=[])
        defaults.update(kw)
        return InterfaceComplianceRule.objects.create(**defaults)

    def _setup(self):
        sw = _switch()
        # AP advertises bridge + wlan-ap; uplink switch advertises bridge + router.
        LLDPNeighbor.objects.create(seen_by=sw, local_interface="1/1/7",
                                    system_name="ap-1", capabilities=["bridge", "wlan-ap"])
        LLDPNeighbor.objects.create(seen_by=sw, local_interface="1/1/8",
                                    system_name="up-sw", capabilities=["bridge", "router"])
        return sw

    def test_bridge_alone_overmatches_both(self):
        self._setup()
        matched = {m[1] for m in ic._matched_interfaces(self._rule())}
        assert matched == {"1/1/7", "1/1/8"}  # the original over-match

    def test_require_router_matches_switch_only(self):
        self._setup()
        matched = {m[1] for m in ic._matched_interfaces(
            self._rule(trigger_require_capabilities=["router"]))}
        assert matched == {"1/1/8"}  # AP (no router) excluded

    def test_exclude_wlan_ap_matches_switch_only(self):
        self._setup()
        matched = {m[1] for m in ic._matched_interfaces(
            self._rule(trigger_exclude_capabilities=["wlan-ap"]))}
        assert matched == {"1/1/8"}

    def test_require_exclude_normalize_full_names(self):
        # require/exclude given as full names still fold to canonical tokens.
        self._setup()
        matched = {m[1] for m in ic._matched_interfaces(
            self._rule(trigger_exclude_capabilities=["wlan-access-point"]))}
        assert matched == {"1/1/8"}

    def test_phone_exclude_bridge(self):
        sw = _switch()
        LLDPNeighbor.objects.create(seen_by=sw, local_interface="1/1/1",
                                    system_name="phone", capabilities=["telephone"])
        LLDPNeighbor.objects.create(seen_by=sw, local_interface="1/1/2",
                                    system_name="phone-sw", capabilities=["telephone", "bridge"])
        matched = {m[1] for m in ic._matched_interfaces(
            self._rule(trigger_value="telephone", trigger_exclude_capabilities=["bridge"]))}
        assert matched == {"1/1/1"}  # phone with a built-in switch (bridge) excluded
