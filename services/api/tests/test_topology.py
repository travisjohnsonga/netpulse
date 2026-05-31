import pytest

from apps.devices import topology
from apps.devices.models import Device, TopologyLink
from apps.telemetry import discovery

pytestmark = pytest.mark.django_db


@pytest.fixture
def devices():
    a = Device.objects.create(hostname="router1", ip_address="10.0.0.1", platform="ios_xe", status="active")
    b = Device.objects.create(hostname="router2", ip_address="10.0.0.2", platform="ios_xe", status="active")
    return a, b


class TestDiscoverLinks:
    def test_creates_link_for_matched_neighbor(self, devices, monkeypatch):
        a, b = devices
        monkeypatch.setattr(discovery, "discover_interfaces", lambda d: [
            {"if_name": "Gi4", "if_speed_mbps": 1000, "lldp_neighbor_hostname": "router2", "lldp_neighbor_port": "Gi4"},
            {"if_name": "Gi5", "if_speed_mbps": 1000, "lldp_neighbor_hostname": "unknown-sw", "lldp_neighbor_port": "Gi1"},
        ])
        found = topology.discover_links(a)
        assert len(found) == 2
        assert sum(1 for f in found if f["matched_device_id"]) == 1
        link = TopologyLink.objects.get(device_a=a, port_a="Gi4")
        assert link.device_b == b and link.port_b == "Gi4" and link.link_speed_mbps == 1000

    def test_matches_by_stripped_hostname(self, devices, monkeypatch):
        a, b = devices  # b.hostname == "router2"
        monkeypatch.setattr(discovery, "discover_interfaces", lambda d: [
            {"if_name": "Gi6", "lldp_neighbor_hostname": "router2.dnstest.local", "lldp_neighbor_port": "Gi1"}])
        found = topology.discover_links(a)
        assert found[0]["matched_device_id"] == b.id

    def test_matches_by_management_ip(self, devices, monkeypatch):
        a, b = devices  # b.ip_address == "10.0.0.2"
        monkeypatch.setattr(discovery, "discover_interfaces", lambda d: [
            {"if_name": "Gi7", "lldp_neighbor_hostname": "mystery", "lldp_neighbor_mgmt_ip": "10.0.0.2",
             "lldp_neighbor_port": "Gi2"}])
        found = topology.discover_links(a)
        assert found[0]["matched_device_id"] == b.id
        assert TopologyLink.objects.filter(device_a=a, device_b=b, port_a="Gi7").exists()

    def test_idempotent_update(self, devices, monkeypatch):
        a, b = devices
        monkeypatch.setattr(discovery, "discover_interfaces", lambda d: [
            {"if_name": "Gi4", "lldp_neighbor_hostname": "router2", "lldp_neighbor_port": "Gi4"}])
        topology.discover_links(a)
        topology.discover_links(a)
        assert TopologyLink.objects.filter(device_a=a).count() == 1

    def test_canonical_link_orders_by_device_id(self, devices):
        a, b = devices  # a.id < b.id
        assert topology.canonical_link(b, "Gi5", a, "Gi4") == (a, "Gi4", b, "Gi5")
        assert topology.canonical_link(a, "Gi4", b, "Gi5") == (a, "Gi4", b, "Gi5")

    def test_bidirectional_discovery_dedupes_to_one_link(self, devices, monkeypatch):
        # Both ends discover each other → one canonical row, not two.
        a, b = devices

        def ifaces(d):
            if d.id == a.id:
                return [{"if_name": "Gi4", "lldp_neighbor_hostname": "router2", "lldp_neighbor_port": "Gi5"}]
            return [{"if_name": "Gi5", "lldp_neighbor_hostname": "router1", "lldp_neighbor_port": "Gi4"}]

        monkeypatch.setattr(discovery, "discover_interfaces", ifaces)
        topology.discover_links(a)
        topology.discover_links(b)
        assert TopologyLink.objects.count() == 1
        link = TopologyLink.objects.get()
        assert (link.device_a_id, link.port_a, link.device_b_id, link.port_b) == (a.id, "Gi4", b.id, "Gi5")


class TestTopologyEndpoint:
    def test_discover_endpoint(self, auth_client, devices, monkeypatch):
        a, b = devices
        monkeypatch.setattr(discovery, "discover_interfaces", lambda d: [
            {"if_name": "Gi4", "lldp_neighbor_hostname": "router2", "lldp_neighbor_port": "Gi4"}])
        resp = auth_client.post(f"/api/devices/{a.id}/topology/discover/")
        assert resp.status_code == 200
        assert resp.json()["count"] == 1 and resp.json()["matched"] == 1

    def test_discover_error(self, auth_client, devices, monkeypatch):
        a, _ = devices
        def boom(d):
            raise discovery.DiscoveryError("no SNMP/SSH")
        monkeypatch.setattr(discovery, "discover_interfaces", boom)
        resp = auth_client.post(f"/api/devices/{a.id}/topology/discover/")
        assert resp.status_code == 502

    def test_topology_includes_edges(self, auth_client, devices):
        a, b = devices
        from django.utils import timezone
        TopologyLink.objects.create(device_a=a, port_a="Gi4", device_b=b, port_b="Gi4",
                                    link_speed_mbps=1000, last_seen=timezone.now())
        resp = auth_client.get("/api/devices/topology/")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["nodes"]) == 2
        assert len(body["edges"]) == 1
        e = body["edges"][0]
        assert e["source"] == str(a.id) and e["target"] == str(b.id)
        assert e["port_a"] == "Gi4" and e["speed_mbps"] == 1000

    def test_topology_depth_filter(self, auth_client):
        # a — b — c (chain). depth=1 from a → {a,b}.
        from django.utils import timezone
        a = Device.objects.create(hostname="a", ip_address="10.1.0.1", status="active")
        b = Device.objects.create(hostname="b", ip_address="10.1.0.2", status="active")
        c = Device.objects.create(hostname="c", ip_address="10.1.0.3", status="active")
        now = timezone.now()
        TopologyLink.objects.create(device_a=a, port_a="p1", device_b=b, port_b="p1", last_seen=now)
        TopologyLink.objects.create(device_a=b, port_a="p2", device_b=c, port_b="p2", last_seen=now)
        resp = auth_client.get(f"/api/devices/topology/?device={a.id}&depth=1")
        labels = {n["label"] for n in resp.json()["nodes"]}
        assert labels == {"a", "b"}

    def test_site_filter(self, auth_client):
        from apps.devices.models import Site
        s = Site.objects.create(name="DC-T")
        Device.objects.create(hostname="in-site", ip_address="10.2.0.1", status="active", site=s)
        Device.objects.create(hostname="no-site", ip_address="10.2.0.2", status="active")
        resp = auth_client.get(f"/api/devices/topology/?site={s.id}")
        labels = {n["label"] for n in resp.json()["nodes"]}
        assert labels == {"in-site"}
