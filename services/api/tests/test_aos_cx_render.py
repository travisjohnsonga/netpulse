"""AOS-CX REST-JSON → CLI rendering (display/diff/storage)."""
import hashlib
import json

import pytest

from apps.devices import aos_cx_render as r

# Real AOS-CX shape: System.hostname, VLAN keyed by id (+name), split
# Interface/Port sections with URL-encoded keys and string-boolean stp flags.
AOS_JSON = json.dumps({
    "System": {"hostname": "sw-test"},
    "VLAN": {"1": {"id": 1}, "10": {"id": 10, "name": "Users"}, "20": {"id": 20, "name": "VOIP"}},
    "Interface": {"1%2F1%2F1": {"name": "1/1/1", "description": "AP port"}},
    "Port": {
        "1%2F1%2F1": {"name": "1/1/1", "vlan_mode": "access", "vlan_tag": "10",
                      "stp_config": {"bpdu_guard_enable": "true", "admin_edge_port_enable": "true"},
                      "loop_protect_enable": True},
        "1%2F1%2F2": {"name": "1/1/2", "vlan_mode": "trunk", "vlan_tag": "1",
                      "vlan_trunks": ["1", "10", "20"]},
        "1%2F1%2F10": {"name": "1/1/10", "vlan_mode": "access", "vlan_tag": "20", "admin": "down"},
    },
})


class TestFullRender:
    def test_hostname_vlans_interfaces(self):
        cli = r.aos_cx_json_to_cli(json.loads(AOS_JSON))
        assert "hostname sw-test" in cli
        assert "vlan 10" in cli and "name Users" in cli
        assert "vlan 20" in cli and "name VOIP" in cli
        assert "interface 1/1/1" in cli
        assert "description AP port" in cli
        assert "vlan access 10" in cli
        assert "spanning-tree bpdu-guard" in cli
        assert "spanning-tree port-type admin-edge" in cli
        assert "loop-protect" in cli
        assert "interface 1/1/2" in cli and "vlan trunk allowed 1,10,20" in cli

    def test_interfaces_sorted_numerically(self):
        cli = r.aos_cx_json_to_cli(json.loads(AOS_JSON))
        # 1/1/2 must precede 1/1/10 (numeric, not lexicographic).
        assert cli.index("interface 1/1/1") < cli.index("interface 1/1/2") < cli.index("interface 1/1/10")

    def test_admin_down_interface_shut(self):
        cli = r.aos_cx_json_to_cli(json.loads(AOS_JSON))
        block = cli.split("interface 1/1/10")[1].split("!")[0]
        assert "shutdown" in block and "no shutdown" not in block

    def test_non_dict_returns_empty(self):
        assert r.aos_cx_json_to_cli("not a dict") == ""
        assert r.aos_cx_json_to_cli(None) == ""


class TestRenderConfigContent:
    def test_aos_cx_json_rendered_to_cli(self):
        out = r.render_config_content(AOS_JSON, "aos_cx")
        assert out.startswith("hostname sw-test")
        assert "interface 1/1/1" in out

    def test_aos_cx_cli_passthrough(self):
        cli = "hostname sw1\n!\ninterface 1/1/1\n    no shutdown\n"
        assert r.render_config_content(cli, "aos_cx") == cli

    def test_non_aos_platform_passthrough(self):
        # A non-AOS-CX platform is never reinterpreted, even if it were JSON.
        assert r.render_config_content(AOS_JSON, "ios_xe") == AOS_JSON

    def test_empty_passthrough(self):
        assert r.render_config_content("", "aos_cx") == ""

    def test_malformed_json_passthrough(self):
        bad = "{ not valid json"
        assert r.render_config_content(bad, "aos_cx") == bad


class TestSingleInterface:
    def test_extract_one_interface(self):
        block = r.aos_cx_json_interface(json.loads(AOS_JSON), "1/1/1")
        assert block.startswith("interface 1/1/1")
        assert "vlan access 10" in block and "trunk" not in block

    def test_missing_interface_empty(self):
        assert r.aos_cx_json_interface(json.loads(AOS_JSON), "9/9/9") == ""


@pytest.mark.django_db
class TestSerializerRenderedContent:
    def test_rendered_content_for_json_backup(self):
        from django.utils import timezone

        from apps.configbackup.models import DeviceConfig
        from apps.configbackup.serializers import DeviceConfigSerializer
        from apps.devices.models import Device

        dev = Device.objects.create(hostname="sw-test", ip_address="10.0.0.1", platform="aos_cx")
        cfg = DeviceConfig.objects.create(
            device=dev, collected_at=timezone.now(), content=AOS_JSON,
            content_hash=hashlib.sha256(AOS_JSON.encode()).hexdigest())
        data = DeviceConfigSerializer(cfg).data
        # raw `content` stays JSON; `rendered_content` is CLI for display.
        assert data["content"].lstrip().startswith("{")
        assert data["rendered_content"].startswith("hostname sw-test")
        assert "interface 1/1/1" in data["rendered_content"]

    def test_rendered_content_equals_content_for_cli(self):
        from django.utils import timezone

        from apps.configbackup.models import DeviceConfig
        from apps.configbackup.serializers import DeviceConfigSerializer
        from apps.devices.models import Device

        dev = Device.objects.create(hostname="ios-sw", ip_address="10.0.0.2", platform="ios_xe")
        cli = "hostname ios-sw\n!\ninterface Gi1/0/1\n no shutdown\n"
        cfg = DeviceConfig.objects.create(
            device=dev, collected_at=timezone.now(), content=cli,
            content_hash=hashlib.sha256(cli.encode()).hexdigest())
        data = DeviceConfigSerializer(cfg).data
        assert data["rendered_content"] == cli
