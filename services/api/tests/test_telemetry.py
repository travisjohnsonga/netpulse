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
    def test_up_with_description_selected(self):
        assert discovery.should_auto_select(
            {"if_name": "GigabitEthernet0/0", "oper_status": "up", "if_description": "uplink"}) is True

    def test_up_with_lldp_selected(self):
        assert discovery.should_auto_select(
            {"if_name": "Gi0/1", "oper_status": "up", "if_description": "", "lldp_neighbor_hostname": "sw1"}) is True

    def test_up_no_context_not_selected(self):
        assert discovery.should_auto_select(
            {"if_name": "Gi0/2", "oper_status": "up", "if_description": ""}) is False

    def test_down_not_selected(self):
        assert discovery.should_auto_select(
            {"if_name": "Gi0/3", "oper_status": "down", "if_description": "x"}) is False

    @pytest.mark.parametrize("name", ["Loopback0", "Tunnel1", "Null0"])
    def test_excluded_virtual(self, name):
        assert discovery.should_auto_select(
            {"if_name": name, "oper_status": "up", "if_description": "x"}) is False


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
