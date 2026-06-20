"""WAN circuits — CRUD, CIDR validation, utilization, contract/util alerts."""
import datetime

import pytest

from apps.circuits import scheduler as cs
from apps.circuits import utilization as cu
from apps.circuits.models import WanCircuit
from apps.alerts.models import AlertEvent
from apps.devices.models import Device, Site

pytestmark = pytest.mark.django_db


@pytest.fixture
def circuit():
    return WanCircuit.objects.create(
        name="WCO2 Primary Internet", provider="AT&T", circuit_type="dia",
        bandwidth_mbps_download=1000, alert_threshold_pct=80)


class TestCrud:
    def test_create(self, auth_client):
        resp = auth_client.post("/api/circuits/", {
            "name": "Backup LTE", "provider": "Verizon", "circuit_type": "lte",
            "status": "active", "bandwidth_mbps_download": 50,
            "isp_ipv4_block": "203.0.113.0/30",
        }, format="json")
        assert resp.status_code == 201
        body = resp.json()
        assert body["circuit_type_display"] == "LTE/Cellular"
        assert body["bandwidth_mbps"] == 50 and body["upload_mbps"] == 50

    def test_invalid_cidr_rejected(self, auth_client):
        resp = auth_client.post("/api/circuits/", {
            "name": "X", "circuit_type": "internet", "isp_ipv4_block": "not-a-cidr"}, format="json")
        assert resp.status_code == 400
        assert "isp_ipv4_block" in resp.json()

    def test_ipv6_cidr_ok(self, auth_client):
        resp = auth_client.post("/api/circuits/", {
            "name": "X", "circuit_type": "internet", "isp_ipv6_block": "2001:db8::/48"}, format="json")
        assert resp.status_code == 201

    def test_filter_by_site(self, auth_client, circuit):
        site = Site.objects.create(name="WCO2")
        circuit.site = site; circuit.save()
        WanCircuit.objects.create(name="Other", circuit_type="internet")
        r = auth_client.get(f"/api/circuits/?site={site.id}").json()
        assert r["count"] == 1

    def test_upload_fallback_symmetric(self, circuit):
        assert circuit.upload_mbps == 1000  # falls back to download
        circuit.bandwidth_mbps_upload = 500; circuit.save()
        assert circuit.upload_mbps == 500


class TestUtilizationEndpoint:
    def test_unbound_circuit(self, auth_client, circuit):
        body = auth_client.get(f"/api/circuits/{circuit.id}/utilization/").json()
        assert body["bound"] is False

    def test_bound_circuit(self, auth_client, circuit, monkeypatch):
        dev = Device.objects.create(hostname="fw", ip_address="10.0.0.1")
        circuit.device = dev; circuit.interface = "ge-0/0/0"; circuit.save()
        monkeypatch.setattr(cu, "get_circuit_utilization", lambda c, period="24h": {
            "circuit_id": c.id, "name": c.name, "bandwidth_mbps_download": 1000,
            "bandwidth_mbps_upload": 1000, "current": {"rx_mbps": 234.5, "rx_pct": 23.5},
            "history": [], "peak": {}, "p95": {"rx_mbps": 567.2, "rx_pct": 56.7}})
        body = auth_client.get(f"/api/circuits/{circuit.id}/utilization/").json()
        assert body["bound"] is True and body["current"]["rx_pct"] == 23.5


class TestPercentile:
    def test_nearest_rank(self):
        vals = list(range(1, 101))  # 1..100
        assert cu._percentile(vals, 95) == 95
        assert cu._percentile([], 95) is None


class TestContractExpiry:
    def test_fires_at_bucket(self, circuit):
        today = datetime.date(2026, 1, 1)
        circuit.contract_end_date = today + datetime.timedelta(days=30)
        circuit.save()
        assert cs.check_contract_expiry(today=today) == 1
        ev = AlertEvent.objects.filter(labels__alert_type="wan_contract", labels__circuit_id=circuit.id)
        assert ev.count() == 1
        # Same day re-run does not duplicate.
        assert cs.check_contract_expiry(today=today) == 0
        assert ev.count() == 1

    def test_no_fire_between_buckets(self, circuit):
        today = datetime.date(2026, 1, 1)
        circuit.contract_end_date = today + datetime.timedelta(days=45)  # not a bucket
        circuit.save()
        assert cs.check_contract_expiry(today=today) == 0

    def test_cancelled_excluded(self, circuit):
        today = datetime.date(2026, 1, 1)
        circuit.contract_end_date = today + datetime.timedelta(days=7)
        circuit.status = "cancelled"; circuit.save()
        assert cs.check_contract_expiry(today=today) == 0


class TestUtilizationAlert:
    def test_fires_and_resolves(self, circuit):
        over = {"current": {"rx_pct": 92.0, "tx_pct": 10.0, "rx_mbps": 920}}
        assert cs._reconcile_util_alert(circuit, over) is True
        ev = AlertEvent.objects.filter(labels__alert_type="wan_utilization", labels__circuit_id=circuit.id)
        assert ev.filter(state=AlertEvent.State.FIRING).count() == 1
        # Under threshold resolves.
        cs._reconcile_util_alert(circuit, {"current": {"rx_pct": 40.0, "tx_pct": 5.0}})
        assert ev.filter(state=AlertEvent.State.FIRING).count() == 0
        assert ev.filter(state=AlertEvent.State.RESOLVED).count() == 1
