"""Manual topology links — model, CRUD API, topology integration, audit."""
import pytest

from apps.devices import topology
from apps.devices.models import Device, ManualTopologyLink

pytestmark = pytest.mark.django_db


@pytest.fixture
def devices():
    a = Device.objects.create(hostname="fw-01", ip_address="10.0.0.1")
    b = Device.objects.create(hostname="crt-01", ip_address="10.0.0.2")
    return a, b


class TestCrud:
    def test_create_link(self, auth_client, devices):
        a, b = devices
        resp = auth_client.post("/api/topology/manual-links/", {
            "device_a": a.id, "interface_a": "GigE0/0/0",
            "device_b": b.id, "interface_b": "1/1/50",
            "link_type": "wan", "speed_mbps": 1000, "description": "Firewall uplink",
        }, format="json")
        assert resp.status_code == 201
        body = resp.json()
        assert body["device_a_hostname"] == "fw-01" and body["device_b_hostname"] == "crt-01"
        assert body["link_type"] == "wan" and body["link_type_display"] == "WAN Circuit"
        assert ManualTopologyLink.objects.count() == 1

    def test_reject_self_link(self, auth_client, devices):
        a, _ = devices
        resp = auth_client.post("/api/topology/manual-links/", {
            "device_a": a.id, "device_b": a.id, "link_type": "ethernet"}, format="json")
        assert resp.status_code == 400

    def test_unique_constraint(self, auth_client, devices):
        a, b = devices
        payload = {"device_a": a.id, "interface_a": "e0", "device_b": b.id,
                   "interface_b": "e1", "link_type": "ethernet"}
        assert auth_client.post("/api/topology/manual-links/", payload, format="json").status_code == 201
        # Same tuple again → 400 (unique constraint).
        assert auth_client.post("/api/topology/manual-links/", payload, format="json").status_code == 400

    def test_filter_by_device_and_site(self, auth_client, devices):
        from apps.devices.models import Site
        a, b = devices
        site = Site.objects.create(name="WCO2")
        a.site = site; a.save()
        c = Device.objects.create(hostname="other", ip_address="10.0.0.3")
        ManualTopologyLink.objects.create(device_a=a, device_b=b, link_type="ethernet")
        ManualTopologyLink.objects.create(device_a=b, device_b=c, link_type="fiber")
        # device_id filter → links touching a (1).
        r = auth_client.get(f"/api/topology/manual-links/?device_id={a.id}").json()
        assert r["count"] == 1
        # site_id filter → links where an endpoint is at the site (a is).
        r = auth_client.get(f"/api/topology/manual-links/?site_id={site.id}").json()
        assert r["count"] == 1

    def test_delete(self, auth_client, devices):
        a, b = devices
        link = ManualTopologyLink.objects.create(device_a=a, device_b=b, link_type="ethernet")
        assert auth_client.delete(f"/api/topology/manual-links/{link.id}/").status_code == 204
        assert ManualTopologyLink.objects.count() == 0

    def test_viewer_cannot_create(self, viewer_client, devices):
        a, b = devices
        resp = viewer_client.post("/api/topology/manual-links/", {
            "device_a": a.id, "device_b": b.id, "link_type": "ethernet"}, format="json")
        assert resp.status_code == 403


class TestTopologyIntegration:
    def test_manual_edge_built_and_flagged(self, devices):
        a, b = devices
        ManualTopologyLink.objects.create(
            device_a=a, interface_a="GigE0/0/0", device_b=b, interface_b="1/1/50",
            link_type="wan", speed_mbps=1000, description="uplink")
        edges = topology.build_manual_edges(
            ManualTopologyLink.objects.all(), {a.id, b.id})
        assert len(edges) == 1
        e = edges[0]
        assert e["manual"] is True and e["link_type"] == "wan"
        assert e["speed_mbps"] == 1000 and e["description"] == "uplink"
        # Canonical ordering (lower id = source).
        assert e["source"] == str(min(a.id, b.id))

    def test_manual_edge_excluded_when_endpoint_out_of_scope(self, devices):
        a, b = devices
        ManualTopologyLink.objects.create(device_a=a, device_b=b, link_type="ethernet")
        # b not in dev_ids → edge dropped.
        assert topology.build_manual_edges(ManualTopologyLink.objects.all(), {a.id}) == []

    def test_topology_endpoint_includes_manual(self, auth_client, devices):
        a, b = devices
        ManualTopologyLink.objects.create(device_a=a, device_b=b, link_type="mgmt")
        body = auth_client.get("/api/devices/topology/").json()
        manual_edges = [e for e in body["edges"] if e.get("manual")]
        assert len(manual_edges) == 1 and manual_edges[0]["link_type"] == "mgmt"


class TestAudit:
    def test_create_audited(self, auth_client, devices):
        from apps.core.models import AuditLog
        a, b = devices
        auth_client.post("/api/topology/manual-links/", {
            "device_a": a.id, "device_b": b.id, "link_type": "ethernet"}, format="json")
        assert AuditLog.objects.filter(
            event_type=AuditLog.EventType.TOPOLOGY_LINK_CREATED).exists()
