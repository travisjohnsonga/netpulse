"""LLDP neighbor persistence, inventory matching, and the undiscovered API."""
import pytest

from apps.devices import lldp, topology
from apps.devices.models import Device, LLDPNeighbor
from apps.telemetry import discovery

pytestmark = pytest.mark.django_db


@pytest.fixture
def devices():
    a = Device.objects.create(hostname="rtr-01", ip_address="10.0.0.1", platform="ios_xe", status="active")
    b = Device.objects.create(hostname="rtr-02", ip_address="10.0.0.2", platform="ios_xe", status="active")
    return a, b


class TestPlatformGuess:
    @pytest.mark.parametrize("desc,expected", [
        ("Cisco IOS Software, IOS-XE Software, CSR1000V", "ios_xe"),
        ("Cisco IOS XR Software", "ios_xr"),
        ("Cisco IOS Software, 7200 Software", "ios"),
        ("Cisco NX-OS(tm) n9000", "nxos"),
        ("Arista Networks EOS version 4.2", "eos"),
        ("ArubaOS-CX VirtualSwitch", "aos_cx"),
        ("Aruba JL658A 6300M", "aos_cx"),
        ("FortiGate-VM64 FortiOS v7.4.3", "fortios"),
        ("Juniper Networks, Inc. JUNOS 21.4", "junos"),
        ("SonicWall SonicOS Enhanced", "sonicwall"),
        ("Palo Alto Networks PAN-OS 11.0", "panos"),
        ("UniFi Switch USW-24-PoE", "unifi_sw"),
        ("", "other"),
        (None, "other"),
        ("Some random vendor box", "other"),
    ])
    def test_guess(self, desc, expected):
        assert lldp.guess_platform(desc) == expected


class TestCapabilities:
    def test_letter_codes_expand(self):
        assert lldp.normalize_capabilities("B, R") == ["bridge", "router"]

    def test_full_words_passthrough(self):
        assert lldp.normalize_capabilities("bridge router") == ["bridge", "router"]

    def test_list_input(self):
        assert lldp.normalize_capabilities(["Bridge", "WLAN-AP"]) == ["bridge", "wlan-ap"]

    def test_empty(self):
        assert lldp.normalize_capabilities(None) == []
        assert lldp.normalize_capabilities("") == []


class TestChassisType:
    def test_mac(self):
        assert lldp.infer_chassis_id_type("aa:bb:cc:dd:ee:ff") == "mac"

    def test_ip(self):
        assert lldp.infer_chassis_id_type("10.0.0.50") == "network-address"

    def test_other(self):
        assert lldp.infer_chassis_id_type("GigabitEthernet0/1") == ""


class TestPersistence:
    def test_neighbor_persisted_with_fields(self, devices, monkeypatch):
        a, _ = devices
        monkeypatch.setattr(discovery, "discover_interfaces", lambda d: [{
            "if_name": "Gi1",
            "lldp_neighbor_hostname": "unknown-switch-01",
            "lldp_neighbor_port": "GigabitEthernet0/1",
            "lldp_neighbor_desc": "uplink to core",
            "lldp_neighbor_mgmt_ip": "10.0.0.50",
            "lldp_neighbor_chassis_id": "aa:bb:cc:dd:ee:ff",
            "lldp_neighbor_system_desc": "Cisco IOS XE Software",
            "lldp_neighbor_capabilities": "B, R",
        }])
        topology.discover_links(a)
        n = LLDPNeighbor.objects.get(seen_by=a, local_interface="Gi1")
        assert n.system_name == "unknown-switch-01"
        assert n.management_address == "10.0.0.50"
        assert n.chassis_id == "aa:bb:cc:dd:ee:ff"
        assert n.chassis_id_type == "mac"
        assert n.port_id == "GigabitEthernet0/1"
        assert n.port_description == "uplink to core"
        assert n.system_description == "Cisco IOS XE Software"
        assert n.capabilities == ["bridge", "router"]
        assert n.matched_device_id is None
        assert n.first_seen is not None and n.last_seen is not None

    def test_matched_neighbor_links_device(self, devices, monkeypatch):
        a, b = devices
        monkeypatch.setattr(discovery, "discover_interfaces", lambda d: [
            {"if_name": "Gi2", "lldp_neighbor_hostname": "rtr-02", "lldp_neighbor_port": "Gi2"}])
        topology.discover_links(a)
        n = LLDPNeighbor.objects.get(seen_by=a, local_interface="Gi2")
        assert n.matched_device_id == b.id

    def test_idempotent_one_row_per_port(self, devices, monkeypatch):
        a, _ = devices
        monkeypatch.setattr(discovery, "discover_interfaces", lambda d: [
            {"if_name": "Gi1", "lldp_neighbor_hostname": "mystery", "lldp_neighbor_port": "Gi1"}])
        topology.discover_links(a)
        first = LLDPNeighbor.objects.get(seen_by=a, local_interface="Gi1").first_seen
        topology.discover_links(a)
        assert LLDPNeighbor.objects.filter(seen_by=a, local_interface="Gi1").count() == 1
        # first_seen is stable across re-scans.
        assert LLDPNeighbor.objects.get(seen_by=a, local_interface="Gi1").first_seen == first


class TestUndiscoveredEndpoint:
    def _seed(self, seen_by, **kw):
        defaults = dict(seen_by=seen_by, local_interface=kw.pop("local_interface", "Gi1"))
        defaults.update(kw)
        return LLDPNeighbor.objects.create(**defaults)

    def test_lists_only_unknown(self, devices, auth_client):
        a, b = devices
        # Unknown neighbor → listed.
        self._seed(a, local_interface="Gi1", system_name="ghost-sw", management_address="10.9.9.9",
                   system_description="Cisco IOS XE Software")
        # Matched neighbor (FK set) → excluded.
        self._seed(a, local_interface="Gi2", system_name="rtr-02", matched_device=b)
        # Neighbor whose mgmt IP is an existing device → excluded (live re-check).
        self._seed(a, local_interface="Gi3", system_name="x", management_address="10.0.0.2")
        # Neighbor whose system_name matches a device hostname → excluded.
        self._seed(a, local_interface="Gi4", system_name="rtr-02")

        resp = auth_client.get("/api/devices/lldp/undiscovered/")
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 1
        row = body["results"][0]
        assert row["system_name"] == "ghost-sw"
        assert row["in_inventory"] is False
        assert row["guessed_platform"] == "ios_xe"
        assert row["seen_by_device_hostname"] == "rtr-01"
        assert row["seen_on_interface"] == "Gi1"

    def test_count_endpoint(self, devices, auth_client):
        a, b = devices
        self._seed(a, local_interface="Gi1", system_name="ghost-1", management_address="10.9.9.1")
        self._seed(a, local_interface="Gi2", system_name="ghost-2", management_address="10.9.9.2")
        self._seed(a, local_interface="Gi3", system_name="rtr-02", matched_device=b)
        resp = auth_client.get("/api/devices/lldp/undiscovered/count/")
        assert resp.status_code == 200
        assert resp.json() == {"count": 2}

    def test_requires_auth(self, devices, api_client):
        resp = api_client.get("/api/devices/lldp/undiscovered/count/")
        assert resp.status_code in (401, 403)
