import pytest
from apps.cve.models import CVE, DeviceCVE
from apps.devices.models import Device

pytestmark = pytest.mark.django_db


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def device():
    return Device.objects.create(hostname="fw-01", ip_address="10.0.0.1")


@pytest.fixture
def cve():
    return CVE.objects.create(
        cve_id="CVE-2024-1234",
        description="Remote code execution in IOS-XE web UI.",
        severity="critical",
        cvss_score="9.8",
        cvss_vector="CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H",
        source_url="https://nvd.nist.gov/vuln/detail/CVE-2024-1234",
    )


@pytest.fixture
def device_cve(device, cve):
    return DeviceCVE.objects.create(device=device, cve=cve, is_patched=False)


# ── CVE Endpoints ─────────────────────────────────────────────────────────────

class TestCVEEndpoints:
    def test_list_cves(self, auth_client, cve):
        resp = auth_client.get("/api/cve/cves/")
        assert resp.status_code == 200
        assert resp.json()["count"] == 1

    def test_retrieve_cve(self, auth_client, cve):
        resp = auth_client.get(f"/api/cve/cves/{cve.pk}/")
        assert resp.status_code == 200
        data = resp.json()
        assert data["cve_id"] == "CVE-2024-1234"
        assert data["severity"] == "critical"
        assert float(data["cvss_score"]) == 9.8

    def test_cve_create_not_allowed(self, auth_client):
        resp = auth_client.post("/api/cve/cves/", {
            "cve_id": "CVE-2024-9999",
            "description": "Test",
            "severity": "low",
        })
        assert resp.status_code == 405

    def test_cve_delete_not_allowed(self, auth_client, cve):
        resp = auth_client.delete(f"/api/cve/cves/{cve.pk}/")
        assert resp.status_code == 405

    def test_cve_update_not_allowed(self, auth_client, cve):
        resp = auth_client.patch(f"/api/cve/cves/{cve.pk}/", {"severity": "high"})
        assert resp.status_code == 405

    def test_filter_by_severity(self, auth_client, cve):
        CVE.objects.create(cve_id="CVE-2024-5555", description="Low severity", severity="low")
        resp = auth_client.get("/api/cve/cves/?severity=critical")
        assert resp.status_code == 200
        assert all(c["severity"] == "critical" for c in resp.json()["results"])

    def test_search_by_cve_id(self, auth_client, cve):
        CVE.objects.create(cve_id="CVE-2023-0001", description="Another vuln", severity="medium")
        resp = auth_client.get("/api/cve/cves/?search=CVE-2024")
        assert resp.status_code == 200
        ids = [c["cve_id"] for c in resp.json()["results"]]
        assert "CVE-2024-1234" in ids
        assert "CVE-2023-0001" not in ids

    def test_search_by_description(self, auth_client, cve):
        resp = auth_client.get("/api/cve/cves/?search=remote+code+execution")
        assert resp.status_code == 200
        assert resp.json()["count"] == 1

    def test_ordering_by_cvss_score(self, auth_client, cve):
        CVE.objects.create(cve_id="CVE-2024-0001", description="Low", severity="low", cvss_score="3.1")
        resp = auth_client.get("/api/cve/cves/?ordering=-cvss_score")
        assert resp.status_code == 200
        scores = [float(c["cvss_score"]) for c in resp.json()["results"] if c["cvss_score"] is not None]
        assert scores == sorted(scores, reverse=True)

    def test_unauthenticated_rejected(self, api_client):
        resp = api_client.get("/api/cve/cves/")
        assert resp.status_code == 401


# ── DeviceCVE Endpoints ───────────────────────────────────────────────────────

class TestDeviceCVEEndpoints:
    def test_list_device_cves(self, auth_client, device_cve):
        resp = auth_client.get("/api/cve/device-cves/")
        assert resp.status_code == 200
        assert resp.json()["count"] == 1

    def test_retrieve_device_cve(self, auth_client, device_cve, cve):
        resp = auth_client.get(f"/api/cve/device-cves/{device_cve.pk}/")
        assert resp.status_code == 200
        data = resp.json()
        assert data["is_patched"] is False
        assert data["cve_id"] == "CVE-2024-1234"
        assert data["severity"] == "critical"

    def test_device_cve_create_not_allowed(self, auth_client, device, cve):
        resp = auth_client.post("/api/cve/device-cves/", {
            "device": device.pk, "cve": cve.pk,
        })
        assert resp.status_code == 405

    def test_device_cve_delete_not_allowed(self, auth_client, device_cve):
        resp = auth_client.delete(f"/api/cve/device-cves/{device_cve.pk}/")
        assert resp.status_code == 405

    def test_filter_by_device(self, auth_client, device_cve, device):
        other_device = Device.objects.create(hostname="other-fw", ip_address="10.0.0.2")
        other_cve = CVE.objects.create(cve_id="CVE-2024-9999", description="Other", severity="medium")
        DeviceCVE.objects.create(device=other_device, cve=other_cve)
        resp = auth_client.get(f"/api/cve/device-cves/?device={device.pk}")
        assert resp.status_code == 200
        assert all(r["device"] == device.pk for r in resp.json()["results"])

    def test_filter_by_is_patched(self, auth_client, device_cve, device):
        patched_cve = CVE.objects.create(cve_id="CVE-2024-8888", description="Patched", severity="low")
        DeviceCVE.objects.create(device=device, cve=patched_cve, is_patched=True)
        resp = auth_client.get("/api/cve/device-cves/?is_patched=false")
        assert resp.status_code == 200
        assert all(r["is_patched"] is False for r in resp.json()["results"])

    def test_filter_by_cve_severity(self, auth_client, device_cve, device):
        low_cve = CVE.objects.create(cve_id="CVE-2024-7777", description="Low", severity="low")
        DeviceCVE.objects.create(device=device, cve=low_cve)
        resp = auth_client.get("/api/cve/device-cves/?cve__severity=critical")
        assert resp.status_code == 200
        assert all(r["severity"] == "critical" for r in resp.json()["results"])

    def test_unique_constraint_device_cve(self, device, cve):
        from django.db import IntegrityError
        DeviceCVE.objects.create(device=device, cve=cve)
        with pytest.raises(IntegrityError):
            DeviceCVE.objects.create(device=device, cve=cve)

    def test_unauthenticated_rejected(self, api_client):
        resp = api_client.get("/api/cve/device-cves/")
        assert resp.status_code == 401


# ── Model Tests ───────────────────────────────────────────────────────────────

class TestCVEModels:
    def test_cve_str(self, cve):
        assert str(cve) == "CVE-2024-1234"

    def test_cvss_score_nullable(self):
        c = CVE.objects.create(cve_id="CVE-2024-0000", description="No score", severity="none")
        assert c.cvss_score is None

    def test_severity_choices(self):
        for val, _ in CVE.Severity.choices:
            assert val in ("critical", "high", "medium", "low", "none")
