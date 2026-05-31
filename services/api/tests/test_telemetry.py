import pytest

from apps.credentials.models import CredentialProfile
from apps.devices.models import Device
from apps.telemetry import discovery
from apps.telemetry.models import MonitoredInterface, TelemetryConfig

pytestmark = pytest.mark.django_db


@pytest.fixture
def ssh_profile():
    return CredentialProfile.objects.create(
        name="tel-ssh", ssh_enabled=True, ssh_username="netmagic", vault_path="x")


@pytest.fixture
def device(ssh_profile):
    return Device.objects.create(
        hostname="rtr-tel", ip_address="10.9.0.1", vendor="Cisco",
        platform="ios_xe", status="active", credential_profile=ssh_profile)


# ── auto-select logic ─────────────────────────────────────────────────────────


class TestAutoSelect:
    def test_up_with_lldp_selected(self):
        # Network-to-network link: up + has LLDP neighbour → auto-selected.
        assert discovery.should_auto_select(
            {"if_name": "Gi0/1", "oper_status": "up", "if_description": "", "lldp_neighbor_hostname": "sw1"}) is True

    def test_up_description_only_not_selected(self):
        # A description without an LLDP neighbour is an edge/access port — the
        # engineer opts in manually; not auto-selected.
        assert discovery.should_auto_select(
            {"if_name": "GigabitEthernet0/0", "oper_status": "up", "if_description": "uplink"}) is False

    def test_up_no_context_not_selected(self):
        assert discovery.should_auto_select(
            {"if_name": "Gi0/2", "oper_status": "up", "if_description": ""}) is False

    def test_down_with_lldp_not_selected(self):
        # Even with a neighbour, a down interface is not auto-selected.
        assert discovery.should_auto_select(
            {"if_name": "Gi0/3", "oper_status": "down", "lldp_neighbor_hostname": "sw1"}) is False

    @pytest.mark.parametrize("name", ["Loopback0", "Tunnel1", "Null0", "Management1", "mgmt0", "GigabitEthernet0/0/0/0-mgmt"])
    def test_excluded_virtual_and_mgmt(self, name):
        # Excluded types/names are never auto-selected, even with a neighbour.
        assert discovery.should_auto_select(
            {"if_name": name, "oper_status": "up", "lldp_neighbor_hostname": "sw1"}) is False


# ── telemetry-config ──────────────────────────────────────────────────────────


class TestTelemetryConfig:
    def test_get_auto_creates(self, auth_client, device):
        resp = auth_client.get(f"/api/devices/{device.id}/telemetry-config/")
        assert resp.status_code == 200
        assert resp.json()["primary_method"] == "snmp"
        assert resp.json()["collect_cpu"] is True
        assert TelemetryConfig.objects.filter(device=device).count() == 1

    def test_update(self, auth_client, device):
        resp = auth_client.put(f"/api/devices/{device.id}/telemetry-config/",
                               {"primary_method": "gnmi", "collect_fans": False, "gnmi_interval": 15}, format="json")
        assert resp.status_code == 200
        cfg = TelemetryConfig.objects.get(device=device)
        assert cfg.primary_method == "gnmi" and cfg.collect_fans is False and cfg.gnmi_interval == 15

    def test_unauthenticated(self, api_client, device):
        assert api_client.get(f"/api/devices/{device.id}/telemetry-config/").status_code == 401


# ── discovery endpoint ────────────────────────────────────────────────────────


class TestDiscover:
    def test_discover_annotates_auto_select(self, auth_client, device, monkeypatch):
        raw = [
            {"if_index": 1, "if_name": "GigabitEthernet0/0", "if_description": "uplink", "if_speed_mbps": 1000,
             "if_type": "ethernetCsmacd", "oper_status": "up", "admin_status": "up",
             "lldp_neighbor_hostname": "core1", "lldp_neighbor_port": "Gi1/0", "lldp_neighbor_desc": "Gi1/0"},
            {"if_index": 2, "if_name": "Loopback0", "if_description": "", "if_speed_mbps": None,
             "if_type": "softwareLoopback", "oper_status": "up", "admin_status": "up",
             "lldp_neighbor_hostname": None, "lldp_neighbor_port": None, "lldp_neighbor_desc": None},
        ]
        monkeypatch.setattr(discovery, "_discover_via_ssh", lambda *a, **k: raw)
        resp = auth_client.post(f"/api/devices/{device.id}/interfaces/discover/")
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 2 and body["auto_selected"] == 1
        gi, lo = body["interfaces"]
        assert gi["auto_select"] is True and gi["collection_method"] == "snmp"
        assert lo["auto_select"] is False

    def test_discover_error(self, auth_client, device, monkeypatch):
        def boom(*a, **k):
            raise discovery.DiscoveryError("SNMP timeout")
        monkeypatch.setattr(discovery, "_discover_via_ssh", boom)
        resp = auth_client.post(f"/api/devices/{device.id}/interfaces/discover/")
        assert resp.status_code == 502
        assert "SNMP timeout" in resp.json()["error"]


# ── monitored interfaces CRUD ─────────────────────────────────────────────────


class TestInterfaces:
    def test_bulk_save_replaces(self, auth_client, device):
        payload = {"interfaces": [
            {"if_name": "GigabitEthernet0/0", "if_description": "uplink", "if_speed_mbps": 1000,
             "poll_traffic": True, "poll_errors": True, "poll_status": True,
             "collection_method": "snmp", "oper_status": "up"},
            {"if_name": "GigabitEthernet0/1", "collection_method": "gnmi"},
        ]}
        resp = auth_client.post(f"/api/devices/{device.id}/interfaces/", payload, format="json")
        assert resp.status_code == 201
        assert MonitoredInterface.objects.filter(device=device).count() == 2
        gi0 = MonitoredInterface.objects.get(device=device, if_name="GigabitEthernet0/0")
        assert gi0.if_speed_mbps == 1000 and gi0.last_status == "up" and gi0.last_discovered is not None

        # Replace with a single interface.
        resp = auth_client.post(f"/api/devices/{device.id}/interfaces/",
                                {"interfaces": [{"if_name": "TenGigE0/1"}]}, format="json")
        assert resp.status_code == 201
        names = list(MonitoredInterface.objects.filter(device=device).values_list("if_name", flat=True))
        assert names == ["TenGigE0/1"]

    def test_list(self, auth_client, device):
        MonitoredInterface.objects.create(device=device, if_name="Gi0/0")
        resp = auth_client.get(f"/api/devices/{device.id}/interfaces/")
        assert resp.status_code == 200
        assert len(resp.json()) == 1 and resp.json()[0]["if_name"] == "Gi0/0"

    def test_delete_by_name_with_slashes(self, auth_client, device):
        MonitoredInterface.objects.create(device=device, if_name="GigabitEthernet0/0/0")
        resp = auth_client.delete(f"/api/devices/{device.id}/interfaces/GigabitEthernet0/0/0/")
        assert resp.status_code == 204
        assert MonitoredInterface.objects.filter(device=device).count() == 0

    def test_unique_per_device(self, auth_client, device):
        MonitoredInterface.objects.create(device=device, if_name="Gi0/0")
        with pytest.raises(Exception):
            MonitoredInterface.objects.create(device=device, if_name="Gi0/0")


# ── config generation + push ──────────────────────────────────────────────────


class TestConfigGenerate:
    def test_generate_cisco_sections(self, auth_client, device, settings):
        settings.COLLECTOR_IP = "192.168.98.134"
        resp = auth_client.get(f"/api/devices/{device.id}/telemetry-config/generate/")
        assert resp.status_code == 200
        body = resp.json()
        assert body["platform"] == "ios_xe" and body["collector_ip"] == "192.168.98.134"
        secs = body["sections"]
        assert set(secs.keys()) == {"snmp", "syslog", "gnmi", "netflow"}
        # SNMP enabled by default (primary_method=snmp); config references collector.
        assert secs["snmp"]["enabled"] is True
        assert "192.168.98.134" in secs["snmp"]["config"]
        assert "logging host 192.168.98.134" in secs["syslog"]["config"]
        assert "192.168.98.134" in body["full_config"]

    def test_cisco_xe_syslog_has_origin_id(self, auth_client, device, settings):
        settings.COLLECTOR_IP = "192.168.98.134"
        resp = auth_client.get(f"/api/devices/{device.id}/telemetry-config/generate/")
        syslog = resp.json()["sections"]["syslog"]["config"]
        assert "logging origin-id hostname" in syslog
        assert "logging host 192.168.98.134" in syslog

    def test_nxos_uses_logging_server(self, auth_client, ssh_profile, settings):
        settings.COLLECTOR_IP = "10.0.0.20"
        from apps.devices.models import Device
        d = Device.objects.create(hostname="nx1", ip_address="10.0.0.21", vendor="Cisco",
                                  platform="nxos", status="active", credential_profile=ssh_profile)
        syslog = auth_client.get(f"/api/devices/{d.id}/telemetry-config/generate/").json()["sections"]["syslog"]["config"]
        assert "logging origin-id hostname" in syslog
        assert "logging server 10.0.0.20" in syslog
        assert "logging host" not in syslog  # not IOS-XE syntax

    def test_xr_uses_hostnameprefix(self, auth_client, ssh_profile, settings):
        settings.COLLECTOR_IP = "10.0.0.30"
        from apps.devices.models import Device
        d = Device.objects.create(hostname="xr1", ip_address="10.0.0.31", vendor="Cisco",
                                  platform="ios_xr", status="active", credential_profile=ssh_profile)
        syslog = auth_client.get(f"/api/devices/{d.id}/telemetry-config/generate/").json()["sections"]["syslog"]["config"]
        assert "logging hostnameprefix xr1" in syslog
        assert "logging 10.0.0.30" in syslog

    def test_generate_fortios_sections(self, auth_client, ssh_profile, settings):
        settings.COLLECTOR_IP = "10.0.0.40"
        from apps.devices.models import Device
        d = Device.objects.create(hostname="fgt1", ip_address="10.0.0.41", management_ip="10.0.0.41",
                                  vendor="Fortinet", platform="fortios", status="active",
                                  credential_profile=ssh_profile)
        secs = auth_client.get(f"/api/devices/{d.id}/telemetry-config/generate/").json()["sections"]
        # SNMP: FortiOS community config referencing the collector.
        assert "config system snmp community" in secs["snmp"]["config"]
        assert "10.0.0.40/32" in secs["snmp"]["config"]
        # Syslog: FortiOS syslogd setting.
        assert "config log syslogd setting" in secs["syslog"]["config"]
        assert 'set server "10.0.0.40"' in secs["syslog"]["config"]
        # NetFlow: collector + source mgmt IP.
        assert "config system netflow" in secs["netflow"]["config"]
        assert "set collector-ip 10.0.0.40" in secs["netflow"]["config"]
        assert "set source-ip 10.0.0.41" in secs["netflow"]["config"]
        # gNMI: not supported — message instead of a subscription.
        assert "does not support gNMI" in secs["gnmi"]["config"]

    def test_generate_juniper_jti_telemetry(self, auth_client, ssh_profile, settings):
        # Juniper streams via JTI (Junos Telemetry Interface) "set services
        # analytics …", not OpenConfig gNMI — the multi-vendor generator emits it.
        settings.COLLECTOR_IP = "10.0.0.10"
        from apps.devices.models import Device
        d = Device.objects.create(hostname="jnpr", ip_address="10.0.0.7", vendor="Juniper",
                                  platform="junos", status="active", credential_profile=ssh_profile)
        resp = auth_client.get(f"/api/devices/{d.id}/telemetry-config/generate/")
        secs = resp.json()["sections"]
        assert secs["snmp"]["config"] is not None
        assert "set system syslog host 10.0.0.10" in secs["syslog"]["config"]
        assert secs["gnmi"]["config"] is not None
        assert "set services analytics" in secs["gnmi"]["config"]


class TestConfigPush:
    def test_push_success(self, auth_client, device, monkeypatch, settings):
        settings.COLLECTOR_IP = "10.0.0.10"
        settings.ALLOW_CONFIG_PUSH = True
        captured = {}

        class FakeConn:
            def send_config_set(self, lines):
                captured.setdefault("lines", []).extend(lines)
                return "applied " + " / ".join(lines[:1])
            def disconnect(self):
                captured["disconnected"] = True

        import apps.telemetry.views as v
        monkeypatch.setattr("netmiko.ConnectHandler", lambda **k: FakeConn())
        resp = auth_client.post(f"/api/devices/{device.id}/telemetry-config/push/",
                                {"sections": ["snmp", "syslog"]}, format="json")
        assert resp.status_code == 200, resp.content
        body = resp.json()
        assert body["success"] is True
        assert set(body["pushed_sections"]) == {"snmp", "syslog"}
        from apps.telemetry.models import ConfigPush
        rec = ConfigPush.objects.filter(device=device).latest("created_at")
        assert rec.success is True and set(rec.sections) == {"snmp", "syslog"}

    def test_push_no_ssh(self, auth_client, settings):
        settings.ALLOW_CONFIG_PUSH = True
        from apps.credentials.models import CredentialProfile
        from apps.devices.models import Device
        p = CredentialProfile.objects.create(name="snmp2", snmpv2c_enabled=True, vault_path="x")
        d = Device.objects.create(hostname="nossh", ip_address="10.0.0.8", platform="ios_xe",
                                  status="active", credential_profile=p)
        resp = auth_client.post(f"/api/devices/{d.id}/telemetry-config/push/",
                                {"sections": ["snmp"]}, format="json")
        assert resp.status_code == 400
        assert resp.json()["success"] is False

    def test_push_blocked_when_disabled(self, auth_client, device, settings):
        # Default safety: ALLOW_CONFIG_PUSH=false → 403, no device connection.
        settings.ALLOW_CONFIG_PUSH = False
        resp = auth_client.post(f"/api/devices/{device.id}/telemetry-config/push/",
                                {"sections": ["snmp"]}, format="json")
        assert resp.status_code == 403
        assert resp.json()["success"] is False
        # Blocked attempt is still audited.
        from apps.telemetry.models import ConfigPush
        rec = ConfigPush.objects.filter(device=device).latest("created_at")
        assert rec.success is False and rec.sections == []

    def test_push_history(self, auth_client, device):
        from apps.telemetry.models import ConfigPush
        ConfigPush.objects.create(device=device, sections=["snmp"], success=True, output="ok")
        resp = auth_client.get(f"/api/devices/{device.id}/telemetry-config/push/")
        assert resp.status_code == 200
        assert len(resp.json()) == 1 and resp.json()[0]["sections"] == ["snmp"]


class TestHealthCollectorIp:
    def test_health_exposes_collector_ip(self, api_client, settings):
        settings.COLLECTOR_IP = "192.168.98.134"
        resp = api_client.get("/api/health/")
        assert resp.status_code == 200
        assert resp.json()["collector_ip"] == "192.168.98.134"


class TestSystemSettings:
    def test_exposes_allow_config_push_flag(self, auth_client, settings):
        settings.ALLOW_CONFIG_PUSH = True
        settings.COLLECTOR_IP = "10.1.2.3"
        resp = auth_client.get("/api/settings/system/")
        assert resp.status_code == 200
        body = resp.json()
        assert body["allow_config_push"] is True
        assert body["collector_ip"] == "10.1.2.3"

    def test_flag_false_by_default(self, auth_client, settings):
        settings.ALLOW_CONFIG_PUSH = False
        resp = auth_client.get("/api/settings/system/")
        assert resp.json()["allow_config_push"] is False


# ── polling intervals (global + per-device override) ──────────────────────────


class TestPollingSettings:
    def test_get_defaults(self, auth_client):
        resp = auth_client.get("/api/settings/polling/")
        assert resp.status_code == 200
        b = resp.json()
        assert b["device_metrics_interval"] == 300 and b["interface_status_interval"] == 60
        assert b["bgp_interval"] == 60 and b["inventory_interval"] == 3600
        assert b["max_concurrent_sessions"] == 10 and b["bulk_get_max_repetitions"] == 25

    def test_update(self, auth_client):
        resp = auth_client.put("/api/settings/polling/", {"interface_traffic_interval": 600, "snmp_timeout": 10}, format="json")
        assert resp.status_code == 200
        from apps.telemetry.models import SNMPGlobalSettings
        g = SNMPGlobalSettings.load()
        assert g.interface_traffic_interval == 600 and g.snmp_timeout == 10

    def test_unauthenticated(self, api_client):
        assert api_client.get("/api/settings/polling/").status_code == 401


class TestEffectiveIntervals:
    def test_uses_global_when_not_overridden(self, auth_client, device):
        resp = auth_client.get(f"/api/devices/{device.id}/telemetry-config/")
        eff = resp.json()["effective_intervals"]
        assert eff["device_metrics"] == 300 and eff["interface_status"] == 60

    def test_uses_device_override(self, auth_client, device):
        auth_client.put(f"/api/devices/{device.id}/telemetry-config/", {
            "override_intervals": True, "device_metrics_interval": 30,
            "interface_traffic_interval": 30,
        }, format="json")
        resp = auth_client.get(f"/api/devices/{device.id}/telemetry-config/")
        eff = resp.json()["effective_intervals"]
        assert eff["device_metrics"] == 30 and eff["interface_traffic"] == 30
        # bgp not overridden → falls back to global
        assert eff["bgp"] == 60

    def test_override_off_ignores_device_values(self, auth_client, device):
        auth_client.put(f"/api/devices/{device.id}/telemetry-config/", {
            "override_intervals": False, "device_metrics_interval": 30,
        }, format="json")
        eff = auth_client.get(f"/api/devices/{device.id}/telemetry-config/").json()["effective_intervals"]
        assert eff["device_metrics"] == 300  # global, since override is off
