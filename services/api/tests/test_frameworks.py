"""
Regulatory framework reporting: seeded catalog, evidence engine, API, PDF.
"""
import pytest

from apps.frameworks import evidence
from apps.frameworks.engine import evaluate_framework
from apps.frameworks.models import FrameworkControl, RegulatoryFramework

pytestmark = pytest.mark.django_db


@pytest.fixture
def seeded():
    from django.core.management import call_command
    call_command("seed_frameworks")


# ── seeding ──────────────────────────────────────────────────────────────────

class TestSeed:
    def test_seeds_six_frameworks(self, seeded):
        keys = set(RegulatoryFramework.objects.values_list("key", flat=True))
        assert keys == {"sox", "iso27001", "nist_csf", "pci_dss", "hipaa", "cis"}
        assert FrameworkControl.objects.count() >= 30

    def test_seed_is_idempotent(self, seeded):
        from django.core.management import call_command
        before = FrameworkControl.objects.count()
        call_command("seed_frameworks")
        assert FrameworkControl.objects.count() == before

    def test_every_control_maps_to_a_real_collector(self, seeded):
        for mk in FrameworkControl.objects.values_list("mapping_key", flat=True).distinct():
            assert mk in evidence.COLLECTORS


# ── evidence engine ──────────────────────────────────────────────────────────

class TestEvidence:
    def test_secrets_management_satisfied_by_design(self):
        # No credentials → OpenBao-backed architecture is still satisfied.
        res = evidence.evaluate_control("secrets_management")
        assert res["status"] == evidence.SATISFIED

    def test_asset_inventory_gap_when_empty(self):
        res = evidence.evaluate_control("asset_inventory")
        assert res["status"] == evidence.GAP

    def test_asset_inventory_satisfied_with_devices(self):
        from apps.devices.models import Device
        Device.objects.create(hostname="d1", ip_address="10.0.0.1")
        res = evidence.evaluate_control("asset_inventory")
        assert res["status"] == evidence.SATISFIED
        assert res["metrics"]["total"] == 1

    def test_unknown_collector_is_not_applicable(self):
        res = evidence.evaluate_control("does_not_exist")
        assert res["status"] == evidence.NOT_APPLICABLE


# ── framework evaluation ─────────────────────────────────────────────────────

class TestEvaluate:
    def test_coverage_and_counts(self, seeded):
        fw = RegulatoryFramework.objects.get(key="cis")
        report = evaluate_framework(fw)
        assert report["framework"]["key"] == "cis"
        assert report["total_controls"] == len(report["controls"])
        assert 0 <= report["coverage"] <= 100
        # counts sum to total
        assert sum(report["counts"].values()) == report["total_controls"]
        for c in report["controls"]:
            assert c["status"] in {"satisfied", "partial", "gap", "not_applicable"}
            assert c["summary"]


# ── API ──────────────────────────────────────────────────────────────────────

class TestApi:
    def test_list_frameworks(self, seeded, auth_client):
        resp = auth_client.get("/api/frameworks/")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body) == 6
        assert {f["key"] for f in body} == {"sox", "iso27001", "nist_csf", "pci_dss", "hipaa", "cis"}
        assert all("coverage" in f and "counts" in f for f in body)

    def test_retrieve_framework_controls(self, seeded, auth_client):
        resp = auth_client.get("/api/frameworks/pci_dss/")
        assert resp.status_code == 200
        body = resp.json()
        assert body["framework"]["key"] == "pci_dss"
        assert len(body["controls"]) >= 5

    def test_retrieve_unknown_404(self, seeded, auth_client):
        assert auth_client.get("/api/frameworks/nope/").status_code == 404

    def test_requires_auth(self, seeded, api_client):
        assert api_client.get("/api/frameworks/").status_code in (401, 403)

    def test_pdf_report(self, seeded, auth_client):
        resp = auth_client.get("/api/frameworks/hipaa/report/")
        assert resp.status_code == 200
        assert resp["Content-Type"] == "application/pdf"
        assert resp.content[:5] == b"%PDF-"
        assert "attachment" in resp["Content-Disposition"]
