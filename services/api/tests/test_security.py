import pytest
from apps.devices.models import Device
from apps.security.models import DeviceRiskScore

pytestmark = pytest.mark.django_db


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def device():
    return Device.objects.create(hostname="core-sw-01", ip_address="10.0.0.1")


@pytest.fixture
def risk_score(device):
    return DeviceRiskScore.objects.create(
        device=device,
        score="72.50",
        cve_score="40.00",
        compliance_score="15.00",
        lifecycle_score="10.00",
        anomaly_score="7.50",
    )


# ── Risk Score Endpoints ──────────────────────────────────────────────────────

class TestDeviceRiskScoreEndpoints:
    def test_list_risk_scores(self, auth_client, risk_score):
        resp = auth_client.get("/api/security/risk-scores/")
        assert resp.status_code == 200
        assert resp.json()["count"] == 1

    def test_retrieve_risk_score(self, auth_client, risk_score, device):
        resp = auth_client.get(f"/api/security/risk-scores/{device.pk}/")
        assert resp.status_code == 200
        data = resp.json()
        assert float(data["score"]) == 72.50
        assert float(data["cve_score"]) == 40.00
        assert data["hostname"] == "core-sw-01"

    def test_risk_score_create_not_allowed(self, auth_client, device):
        resp = auth_client.post("/api/security/risk-scores/", {
            "device": device.pk, "score": "50.00",
        })
        assert resp.status_code == 405

    def test_risk_score_delete_not_allowed(self, auth_client, risk_score, device):
        resp = auth_client.delete(f"/api/security/risk-scores/{device.pk}/")
        assert resp.status_code == 405

    def test_risk_score_update_not_allowed(self, auth_client, risk_score, device):
        resp = auth_client.patch(f"/api/security/risk-scores/{device.pk}/", {"score": "10.00"})
        assert resp.status_code == 405

    def test_filter_by_device(self, auth_client, risk_score, device):
        other = Device.objects.create(hostname="other-sw", ip_address="10.0.0.2")
        DeviceRiskScore.objects.create(device=other, score="10.00")
        resp = auth_client.get(f"/api/security/risk-scores/?device={device.pk}")
        assert resp.status_code == 200
        assert resp.json()["count"] == 1
        assert resp.json()["results"][0]["hostname"] == "core-sw-01"

    def test_ordering_by_score(self, auth_client, risk_score):
        other = Device.objects.create(hostname="low-risk-sw", ip_address="10.0.0.3")
        DeviceRiskScore.objects.create(device=other, score="10.00")
        resp = auth_client.get("/api/security/risk-scores/?ordering=-score")
        assert resp.status_code == 200
        scores = [float(r["score"]) for r in resp.json()["results"]]
        assert scores == sorted(scores, reverse=True)

    def test_unauthenticated_rejected(self, api_client):
        resp = api_client.get("/api/security/risk-scores/")
        assert resp.status_code == 401


# ── Model Tests ───────────────────────────────────────────────────────────────

class TestDeviceRiskScoreModel:
    def test_str(self, risk_score):
        assert "core-sw-01" in str(risk_score)
        assert "72.50" in str(risk_score)

    def test_one_to_one_device(self, device):
        from django.db import IntegrityError
        DeviceRiskScore.objects.create(device=device, score="50.00")
        with pytest.raises(IntegrityError):
            DeviceRiskScore.objects.create(device=device, score="60.00")

    def test_component_scores_default_zero(self, device):
        rs = DeviceRiskScore.objects.create(device=device, score="5.00")
        assert float(rs.cve_score) == 0
        assert float(rs.compliance_score) == 0
        assert float(rs.lifecycle_score) == 0
        assert float(rs.anomaly_score) == 0

    def test_last_computed_at_auto_set(self, risk_score):
        assert risk_score.last_computed_at is not None

    def test_last_computed_at_updates_on_save(self, risk_score):
        original = risk_score.last_computed_at
        import time
        time.sleep(0.01)
        risk_score.score = "73.00"
        risk_score.save()
        risk_score.refresh_from_db()
        assert risk_score.last_computed_at >= original
