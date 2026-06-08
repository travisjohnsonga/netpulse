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


# ── FortiOS interface parsing ─────────────────────────────────────────────────


class TestFortiOSParse:
    SAMPLE = (
        "== [ port1 ]\n"
        "name: port1   mode: static   ip: 192.168.1.99 255.255.255.0   status: up"
        "   type: physical   speed: 1000Mbps   alias: WAN1\n"
        "== [ port2 ]\n"
        "name: port2   mode: dhcp   ip: 0.0.0.0 0.0.0.0   status: down"
        "   type: physical   speed: auto\n"
        "== [ loop1 ]\n"
        "name: loop1   mode: static   ip: 10.0.0.1 255.255.255.255   status: up"
        "   type: loopback\n"
    )

    def test_parses_physical_interfaces(self):
        rows = discovery.parse_fortios_interfaces(self.SAMPLE)
        names = [r["if_name"] for r in rows]
        assert names == ["port1", "port2"]  # loopback skipped

    def test_status_and_speed(self):
        rows = {r["if_name"]: r for r in discovery.parse_fortios_interfaces(self.SAMPLE)}
        assert rows["port1"]["oper_status"] == "up"
        assert rows["port1"]["if_speed_mbps"] == 1000
        assert rows["port1"]["if_description"] == "WAN1"
        assert rows["port2"]["oper_status"] == "down"

    def test_flat_form_without_headers(self):
        flat = ("name: wan1   status: up   type: physical   speed: 10Gbps\n"
                "name: tunnel.1   status: up   type: tunnel\n")
        rows = discovery.parse_fortios_interfaces(flat)
        assert [r["if_name"] for r in rows] == ["wan1"]  # tunnel skipped
        assert rows[0]["if_speed_mbps"] == 10000

    def test_empty(self):
        assert discovery.parse_fortios_interfaces("") == []

    def test_lldp_merge(self):
        lldp = (
            "Interface: port1\n"
            "    System Name: core-sw-1\n"
            "    Port ID: GigabitEthernet1/0/24\n"
            "    Port Description: uplink-to-fw\n"
            "    Management Address: 10.0.0.5\n"
        )
        nb = discovery.parse_fortios_lldp(lldp)
        key = discovery._norm("port1")
        assert nb[key]["host"] == "core-sw-1"
        assert nb[key]["port"] == "GigabitEthernet1/0/24"


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
        body = resp.json()
        assert body["interfaces"] == []
        # The raw exception text must not leak to the client (CodeQL: information
        # exposure through an exception) — a safe, generic message is returned.
        assert "SNMP timeout" not in body["error"]
        assert body["error"] == "Interface discovery failed."

    def test_snmpv3_routes_to_snmp_walk(self, monkeypatch):
        # An SNMPv3-only profile (no v2c, no SSH) must go through the SNMP walk,
        # not raise, and use the v3 port.
        from pysnmp.hlapi.v3arch.asyncio import UsmUserData

        profile = CredentialProfile.objects.create(
            name="v3-disc", snmpv3_enabled=True, snmpv3_username="netpulse-mon",
            snmpv3_port=1610, vault_path="x")
        dev = Device.objects.create(
            hostname="v3rtr", ip_address="10.9.0.2", management_ip="10.9.0.2",
            platform="ios_xe", status="active", credential_profile=profile)
        raw = [
            {"if_name": "GigabitEthernet0/0", "oper_status": "up",
             "lldp_neighbor_hostname": "core1"},
            {"if_name": "Loopback0", "oper_status": "up", "lldp_neighbor_hostname": None},
        ]
        captured = {}

        def fake(host, port, auth_data):
            captured.update(host=host, port=port, auth=auth_data)
            return raw
        monkeypatch.setattr(discovery, "_discover_via_snmp", fake)

        out = discovery.discover_interfaces(dev)
        assert captured["host"] == "10.9.0.2"
        assert captured["port"] == 1610                       # v3 port, not 161
        assert isinstance(captured["auth"], UsmUserData)      # v3 auth, not community
        gi, lo = out
        assert gi["auto_select"] is True and gi["collection_method"] == "snmp"
        assert lo["auto_select"] is False                     # loopback, no neighbour


# ── SNMP auth object ──────────────────────────────────────────────────────────


class TestBuildSnmpAuth:
    def test_v3_returns_usm_user_data(self):
        from apps.credentials.snmp_auth import build_snmp_auth
        from pysnmp.hlapi.v3arch.asyncio import UsmUserData

        profile = CredentialProfile(
            snmpv3_enabled=True, snmpv3_username="netpulse-mon",
            snmpv3_auth_protocol="SHA", snmpv3_priv_protocol="AES",
            snmpv3_security_level="authPriv")
        auth = build_snmp_auth(
            profile, {"snmpv3_auth_key": "authkey123", "snmpv3_priv_key": "privkey123"})
        assert isinstance(auth, UsmUserData)

    def test_v2c_returns_community_data(self):
        from apps.credentials.snmp_auth import build_snmp_auth
        from pysnmp.hlapi.v3arch.asyncio import CommunityData

        profile = CredentialProfile(snmpv2c_enabled=True)
        auth = build_snmp_auth(profile, {"snmpv2c_community": "public"})
        assert isinstance(auth, CommunityData)


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

    def test_list_includes_neighbor_device_id(self, auth_client, device):
        # A neighbor that IS in inventory → its device id, so the UI links it.
        neighbor = Device.objects.create(hostname="core1", ip_address="10.9.0.2", status="active")
        MonitoredInterface.objects.create(device=device, if_name="Gi0/0", lldp_neighbor_hostname="core1")
        # A neighbor not in inventory → null (plain text in the UI).
        MonitoredInterface.objects.create(device=device, if_name="Gi0/1", lldp_neighbor_hostname="unknown-sw")
        # No LLDP neighbor at all → null.
        MonitoredInterface.objects.create(device=device, if_name="Gi0/2")

        rows = {r["if_name"]: r for r in auth_client.get(f"/api/devices/{device.id}/interfaces/").json()}
        assert rows["Gi0/0"]["lldp_neighbor_device_id"] == neighbor.id
        assert rows["Gi0/1"]["lldp_neighbor_device_id"] is None
        assert rows["Gi0/2"]["lldp_neighbor_device_id"] is None

    def test_neighbor_resolves_by_ip(self, auth_client, device):
        # An LLDP neighbor name that's an IP resolves against the inet columns.
        neighbor = Device.objects.create(hostname="edge1", ip_address="10.9.0.7", status="active")
        MonitoredInterface.objects.create(device=device, if_name="Gi1/0", lldp_neighbor_hostname="10.9.0.7")
        rows = {r["if_name"]: r for r in auth_client.get(f"/api/devices/{device.id}/interfaces/").json()}
        assert rows["Gi1/0"]["lldp_neighbor_device_id"] == neighbor.id

    def test_non_ip_neighbor_name_does_not_500(self, auth_client, device):
        # A non-IP neighbor name (here a corrupted value) must not be compared
        # against the inet columns — that raised ValueError / 500 on PostgreSQL.
        MonitoredInterface.objects.create(
            device=device, if_name="Gi2/0",
            lldp_neighbor_hostname="# ─── Done ─────────────────")
        resp = auth_client.get(f"/api/devices/{device.id}/interfaces/")
        assert resp.status_code == 200
        rows = {r["if_name"]: r for r in resp.json()}
        assert rows["Gi2/0"]["lldp_neighbor_device_id"] is None


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


class TestSNMPv3Config:
    """SNMPv3 authPriv config generation per platform."""

    def _v3_profile(self, auth="SHA", priv="AES128"):
        return CredentialProfile.objects.create(
            name=f"v3-{auth}-{priv}", snmpv3_enabled=True, snmpv3_username="netpulse-mon",
            snmpv3_auth_protocol=auth, snmpv3_priv_protocol=priv, vault_path="v3")

    def _gen(self, auth_client, platform, vendor, profile):
        d = Device.objects.create(
            hostname=f"v3-{platform}", ip_address="10.5.0.1", management_ip="10.5.0.1",
            vendor=vendor, platform=platform, status="active", credential_profile=profile)
        return auth_client.get(f"/api/devices/{d.id}/telemetry-config/generate/").json()

    def test_cisco_xe_v3(self, auth_client):
        body = self._gen(auth_client, "ios_xe", "Cisco", self._v3_profile("SHA", "AES256"))
        cfg = body["sections"]["snmp"]["config"]
        assert "snmp-server user netpulse-mon" in cfg
        assert "v3 auth sha YOUR-AUTH-KEY-HERE priv aes 256 YOUR-PRIV-KEY-HERE" in cfg
        assert "version 3 auth netpulse-mon" in cfg
        assert body["snmpv3"] is True
        assert body["snmp_warning"] == ""
        assert "community" not in cfg.lower()

    def test_nxos_v3_uses_dedicated_syntax(self, auth_client):
        cfg = self._gen(auth_client, "nxos", "Cisco", self._v3_profile("SHA", "AES128"))["sections"]["snmp"]["config"]
        # NX-OS: "aes-128" not "aes 128", no view/group RW lines.
        assert "snmp-server user netpulse-mon V3GROUP auth sha" in cfg
        assert "priv aes-128" in cfg
        assert "snmp-server view" not in cfg

    def test_arista_v3(self, auth_client):
        cfg = self._gen(auth_client, "eos", "Arista", self._v3_profile("MD5", "AES128"))["sections"]["snmp"]["config"]
        assert "snmp-server user netpulse-mon V3GROUP v3 auth md5" in cfg
        assert "priv aes" in cfg

    def test_juniper_v3(self, auth_client):
        cfg = self._gen(auth_client, "junos", "Juniper", self._v3_profile("SHA256", "AES128"))["sections"]["snmp"]["config"]
        assert "authentication-sha256 authentication-key YOUR-AUTH-KEY-HERE" in cfg
        assert "privacy-aes128 privacy-key YOUR-PRIV-KEY-HERE" in cfg
        assert "set snmp community" not in cfg  # no v2c when v3 active

    def test_fortios_v3(self, auth_client):
        cfg = self._gen(auth_client, "fortios", "Fortinet", self._v3_profile("SHA", "AES128"))["sections"]["snmp"]["config"]
        assert "config system snmp user" in cfg
        assert "set security-level auth-priv" in cfg
        assert "set auth-proto sha1" in cfg
        assert "set priv-proto aes" in cfg
        assert "config system snmp community" not in cfg

    def test_v2c_warning_when_not_v3(self, auth_client, ssh_profile):
        body = self._gen(auth_client, "ios_xe", "Cisco", ssh_profile)
        assert body["snmpv3"] is False
        assert "plaintext" in body["snmp_warning"]


class TestConfigPush:
    def test_push_success(self, auth_client, device, monkeypatch, settings):
        settings.COLLECTOR_IP = "10.0.0.10"
        settings.ALLOW_CONFIG_PUSH = True
        captured = {}

        class FakeConn:
            def send_config_set(self, lines, **kwargs):
                captured.setdefault("lines", []).extend(lines)
                captured["read_timeout"] = kwargs.get("read_timeout")
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


class TestConfigSanitize:
    def test_sanitize_replaces_unicode(self):
        from apps.telemetry.config_gen import sanitize_config_for_push
        assert sanitize_config_for_push("a\u2014b") == "a-b"      # em dash
        assert sanitize_config_for_push("a\u2013b") == "a-b"      # en dash
        assert sanitize_config_for_push("\u2018x\u2019") == "'x'"  # single quotes
        assert sanitize_config_for_push("\u201cy\u201d") == '\"y\"'  # double quotes
        assert sanitize_config_for_push("a\u2026b") == "a...b"   # ellipsis
        assert sanitize_config_for_push("a\u00a0b") == "a b"      # nbsp
        assert sanitize_config_for_push("a\u2500b") == "a-b"      # box drawing
        assert sanitize_config_for_push("a\u2014b").isascii()

    def test_sanitize_replaces_residual_nonascii_with_question(self):
        from apps.telemetry.config_gen import sanitize_config_for_push
        s = sanitize_config_for_push("snmp 中 server")  # CJK char → ?
        assert s.isascii() and "?" in s

    def test_section_lines_strips_comments_and_sanitizes(self):
        from apps.telemetry.config_gen import section_lines
        cfg = "! a comment — noise\nsnmp-server community netpulse\n\n!another\nlogging host 1.2.3.4"
        lines = section_lines(cfg)
        assert lines == ["snmp-server community netpulse", "logging host 1.2.3.4"]
        assert all(l.isascii() for l in lines)

    def test_generated_gnmi_is_ascii(self, auth_client, device, settings):
        # The IOS-XE gNMI header used an em dash; generated config must be ASCII.
        settings.COLLECTOR_IP = "10.0.0.9"
        secs = auth_client.get(f"/api/devices/{device.id}/telemetry-config/generate/").json()["sections"]
        assert secs["gnmi"]["config"].isascii()
        assert "monitored interface" in secs["gnmi"]["config"]


# ── AOS-CX config generation (Stage 3) ──────────────────────────────────────────


class TestAosCxConfigGen:
    @pytest.fixture
    def v3_profile(self):
        return CredentialProfile.objects.create(
            name="aoscx-v3", snmpv3_enabled=True, snmpv3_username="netpulse-mon",
            snmpv3_auth_protocol="sha", snmpv3_priv_protocol="aes128", vault_path="x")

    @pytest.fixture
    def aos_device(self, v3_profile):
        return Device.objects.create(
            hostname="cx1", ip_address="10.0.0.51", management_ip="10.0.0.51",
            vendor="aruba", platform="aos_cx", status="active",
            credential_profile=v3_profile)

    def test_generate_aos_cx_sections(self, auth_client, aos_device, settings):
        settings.COLLECTOR_IP = "10.0.0.50"
        secs = auth_client.get(
            f"/api/devices/{aos_device.id}/telemetry-config/generate/").json()["sections"]

        # SNMPv3 authPriv on the mgmt VRF.
        snmp = secs["snmp"]["config"]
        assert "snmp-server vrf mgmt" in snmp
        assert "snmpv3 user netpulse-mon auth sha auth-pass YOUR-AUTH-KEY-HERE" in snmp
        assert "priv aes priv-pass YOUR-PRIV-KEY-HERE" in snmp
        assert "snmp-server host 10.0.0.50 vrf mgmt version 3 netpulse-mon" in snmp
        assert "snmp-server enable trap snmp" in snmp

        # Syslog: AOS-CX "logging <ip> severity ...".
        syslog = secs["syslog"]["config"]
        assert "logging 10.0.0.50 severity info" in syslog
        assert "logging on" in syslog

        # The "netflow" slot carries sFlow (AOS-CX has no NetFlow).
        sflow = secs["netflow"]["config"]
        assert "sflow 10.0.0.50 vrf mgmt" in sflow
        assert "sflow sampling 512" in sflow
        assert "sflow polling 30" in sflow
        assert "sflow enable" in sflow

        # gNMI: dial-IN note + REST enable + OpenConfig paths, port 8443.
        gnmi = secs["gnmi"]["config"]
        assert "DIAL-IN" in gnmi
        assert "8443" in gnmi
        assert "https-server rest access-mode read-write" in gnmi
        assert "https-server vrf mgmt" in gnmi
        assert "/system/cpus/cpu[index=0]/state/usage/instant" in gnmi
        assert gnmi.isascii()

    def test_aos_cx_v2c_fallback_warns(self, auth_client, settings):
        settings.COLLECTOR_IP = "10.0.0.50"
        prof = CredentialProfile.objects.create(
            name="aoscx-v2c", snmpv2c_enabled=True, vault_path="x")
        d = Device.objects.create(
            hostname="cx2", ip_address="10.0.0.52", management_ip="10.0.0.52",
            vendor="aruba", platform="aos_cx", status="active", credential_profile=prof)
        body = auth_client.get(f"/api/devices/{d.id}/telemetry-config/generate/").json()
        snmp = body["sections"]["snmp"]["config"]
        assert "snmp-server vrf mgmt" in snmp
        assert "snmp-server community" in snmp
        assert body["snmp_warning"]  # v2c plaintext warning surfaced

    def test_generate_aos_cx_gnmi_unit(self):
        from apps.telemetry import gnmi_subscriptions as gs
        d = Device.objects.create(
            hostname="cx3", ip_address="10.0.0.60", management_ip="10.0.0.60",
            platform="aos_cx", status="active")
        for i, n in enumerate(["1/1/1", "1/1/2"], start=1):
            MonitoredInterface.objects.create(device=d, if_index=i, if_name=n)
        ifaces = list(d.monitored_interfaces.all().order_by("if_index"))
        cfg = gs.generate_aos_cx_gnmi(d, "10.0.0.50", ifaces)
        assert "10.0.0.60:8443" in cfg                      # dial-in target = device
        assert "https-server vrf mgmt" in cfg
        assert "/system/memory/state/used" in cfg
        assert "/system/memory/state/free" in cfg
        assert "neighbor/state" in cfg                       # BGP path
        assert "interface[name='1/1/1']/state/counters" in cfg
        assert "interface[name='1/1/2']/state/counters" in cfg

    def test_aos_cx_dispatch_via_generate_push_config(self):
        from apps.telemetry import gnmi_subscriptions as gs
        d = Device.objects.create(
            hostname="cx4", ip_address="10.0.0.61", management_ip="10.0.0.61",
            platform="aos_cx", status="active")
        cfg = gs.generate_push_config(d, "10.0.0.50", [])
        assert "AOS-CX gNMI is DIAL-IN" in cfg

    def test_aos_cx_openconfig_field_map(self):
        from apps.devices.metrics_influx import FIELD_MAP
        assert FIELD_MAP["/system/cpus/cpu/state/usage/instant"] == "cpu_pct"
        assert FIELD_MAP["/system/memory/state/used"] == "memory_used_bytes"
        assert FIELD_MAP["/system/memory/state/free"] == "memory_free_bytes"
