"""Integration: topology endpoint shape + canonical_link dedup (complementary)."""
import pytest

from apps.devices import topology
from apps.devices.models import Device, TopologyLink

pytestmark = pytest.mark.django_db


@pytest.fixture
def pair():
    a = Device.objects.create(hostname="topo-a", ip_address="10.10.0.1")
    b = Device.objects.create(hostname="topo-b", ip_address="10.10.0.2")
    return a, b


class TestTopologyEndpoint:
    def test_empty_topology_shape(self, auth_client):
        resp = auth_client.get("/api/devices/topology/")
        assert resp.status_code == 200
        body = resp.json()
        assert "nodes" in body and "edges" in body
        assert body["nodes"] == [] or isinstance(body["nodes"], list)

    def test_topology_includes_link(self, auth_client, pair):
        a, b = pair
        TopologyLink.objects.create(device_a=a, port_a="Gi1", device_b=b, port_b="Gi2")
        resp = auth_client.get("/api/devices/topology/")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["nodes"]) == 2
        assert len(body["edges"]) == 1

    def test_requires_auth(self, api_client):
        assert api_client.get("/api/devices/topology/").status_code == 401


class TestCanonicalLinkDedup:
    def test_canonical_orders_by_device_id(self, pair):
        a, b = pair  # a.id < b.id
        assert topology.canonical_link(b, "Gi2", a, "Gi1") == (a, "Gi1", b, "Gi2")
        assert topology.canonical_link(a, "Gi1", b, "Gi2") == (a, "Gi1", b, "Gi2")

    def test_both_directions_collapse_to_one_row(self, pair):
        a, b = pair
        # Discovery from A's side.
        d1, p1, d2, p2 = topology.canonical_link(a, "Gi1", b, "Gi2")
        TopologyLink.objects.update_or_create(
            device_a=d1, port_a=p1, device_b=d2, port_b=p2, defaults={})
        # Discovery from B's side (reversed) canonicalizes to the same tuple.
        d1, p1, d2, p2 = topology.canonical_link(b, "Gi2", a, "Gi1")
        TopologyLink.objects.update_or_create(
            device_a=d1, port_a=p1, device_b=d2, port_b=p2, defaults={})
        assert TopologyLink.objects.count() == 1
