"""
Regulatory framework reporting: seeded catalog, evidence engine, API, PDF.
"""
import pytest
from django.test import override_settings

from apps.frameworks import evidence, scope
from apps.frameworks.engine import evaluate_framework
from apps.frameworks.models import FrameworkControl, RegulatoryFramework

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _unscoped_by_default(settings):
    """Pin the applicable-frameworks scope to UNSET (= all frameworks apply) for
    every test in this module, so the suite is deterministic regardless of the
    host's ambient APPLICABLE_COMPLIANCE_FRAMEWORKS (e.g. a lab scoped to
    ``sox,iso27001`` would otherwise break the "unset = all 6" baseline tests).
    Scoping tests override this explicitly via override_settings / scoped_sox_iso.
    """
    settings.APPLICABLE_COMPLIANCE_FRAMEWORKS = []


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


# ── applicable-framework scope ─────────────────────────────────────────────────
# Guarantee: with applicable=sox,iso27001 there is NO surface (API list, score,
# aggregate, report, denominator, alert) where an out-of-scope framework (pci_dss/
# hipaa) appears as failing/partial/non-compliant or as a denominator that implies
# failure. Out-of-scope frameworks are simply not part of the compliance picture.

class TestScopeHelper:
    def test_unset_means_all_apply(self, seeded):
        with override_settings(APPLICABLE_COMPLIANCE_FRAMEWORKS=[]):
            assert scope.applicable_framework_keys() is None
            assert scope.is_framework_applicable("pci_dss") is True
            assert scope.applicable_frameworks().count() == 6
            assert scope.out_of_scope_keys() == set()

    def test_allowlist_limits_scope(self, seeded):
        with override_settings(APPLICABLE_COMPLIANCE_FRAMEWORKS=["sox", "iso27001"]):
            assert scope.applicable_framework_keys() == {"sox", "iso27001"}
            assert scope.is_framework_applicable("sox") is True
            assert scope.is_framework_applicable("pci_dss") is False
            assert set(scope.applicable_frameworks().values_list("key", flat=True)) == {"sox", "iso27001"}
            assert scope.out_of_scope_keys() == {"nist_csf", "pci_dss", "hipaa", "cis"}

    def test_unknown_keys_ignored_fail_closed(self, seeded):
        # A typo can't widen scope to a non-existent framework.
        with override_settings(APPLICABLE_COMPLIANCE_FRAMEWORKS=["sox", "bogus"]):
            assert scope.applicable_framework_keys() == {"sox"}
            assert scope.is_framework_applicable("bogus") is False
            assert set(scope.applicable_frameworks().values_list("key", flat=True)) == {"sox"}

    def test_case_and_whitespace_insensitive(self, seeded):
        with override_settings(APPLICABLE_COMPLIANCE_FRAMEWORKS=[" SOX ", "Iso27001"]):
            assert scope.applicable_framework_keys() == {"sox", "iso27001"}


@pytest.fixture
def scoped_sox_iso(settings):
    """Scope the environment to SOX + ISO 27001 only (pci_dss/hipaa out of scope)."""
    settings.APPLICABLE_COMPLIANCE_FRAMEWORKS = ["sox", "iso27001"]
    return settings


@pytest.mark.usefixtures("scoped_sox_iso")
class TestScopeApi:
    def test_list_excludes_out_of_scope(self, seeded, auth_client):
        body = auth_client.get("/api/frameworks/").json()
        keys = {f["key"] for f in body}
        assert keys == {"sox", "iso27001"}
        assert "pci_dss" not in keys and "hipaa" not in keys

    def test_n_frameworks_denominator_is_applicable_count(self, seeded, auth_client):
        # The /compliance page and TV "Frameworks" stat derive N from this list;
        # it must be the applicable count (2), never 6.
        assert len(auth_client.get("/api/frameworks/").json()) == 2

    def test_out_of_scope_retrieve_404(self, seeded, auth_client):
        # No assessment surface (status/coverage/controls) for an out-of-scope fw.
        assert auth_client.get("/api/frameworks/pci_dss/").status_code == 404
        assert auth_client.get("/api/frameworks/hipaa/").status_code == 404

    def test_out_of_scope_report_404(self, seeded, auth_client):
        # No PDF evidence package for an out-of-scope framework.
        assert auth_client.get("/api/frameworks/pci_dss/report/").status_code == 404

    def test_in_scope_still_accessible(self, seeded, auth_client):
        assert auth_client.get("/api/frameworks/sox/").status_code == 200
        assert auth_client.get("/api/frameworks/sox/report/").status_code == 200


class TestScopeAggregate:
    """The fleet-coverage average + 'N frameworks' are computed over ONLY the
    applicable frameworks (the scoped list the surfaces consume)."""

    @staticmethod
    def _fleet_coverage(body):
        covs = [f["coverage"] for f in body if f["coverage"] is not None]
        return round(sum(covs) / len(covs), 1) if covs else None

    def test_out_of_scope_not_in_aggregate_or_denominator(self, seeded, auth_client):
        full = auth_client.get("/api/frameworks/").json()  # unset → all 6
        assert "pci_dss" in {f["key"] for f in full}  # baseline: present unscoped
        with override_settings(APPLICABLE_COMPLIANCE_FRAMEWORKS=["sox", "iso27001"]):
            scoped = auth_client.get("/api/frameworks/").json()
        assert {f["key"] for f in scoped} == {"sox", "iso27001"}
        # Denominator: only applicable frameworks.
        assert len(scoped) == 2
        # Aggregate (numerator+denominator): equals the average of just the in-scope
        # frameworks — out-of-scope coverage values are excluded entirely, so an
        # out-of-scope framework cannot drag the headline number down.
        assert self._fleet_coverage(scoped) == self._fleet_coverage(
            [f for f in full if f["key"] in {"sox", "iso27001"}])


class TestDeviceScorePathUnaffected:
    """The device compliance summary (apps.reports.compliance_summary →
    apps.compliance.device_score) scores device *config* compliance and never
    counts regulatory-framework controls — so an out-of-scope framework with unmet
    (GAP) controls can't appear in, or drag down, the headline device score."""

    def test_compliance_summary_never_references_frameworks(self, seeded):
        from apps.devices.models import Device
        from apps.reports.compliance_summary import build_compliance_summary
        Device.objects.create(hostname="sw1", ip_address="10.0.0.1", platform="ios")
        with override_settings(APPLICABLE_COMPLIANCE_FRAMEWORKS=["sox"]):
            report = build_compliance_summary()
        blob = str(report).lower()
        for token in ("pci_dss", "hipaa", "framework"):
            assert token not in blob
        assert "avg_score" in report["summary"]


class TestNoFrameworkAlerting:
    """No alerting/notification fires on regulatory-framework status — compliance
    alerts are device-level (config drift, unsaved startup) only. Evaluating a
    framework (in- or out-of-scope) must never create an AlertEvent."""

    def test_evaluating_frameworks_creates_no_alert_events(self, seeded, auth_client):
        from apps.alerts.models import AlertEvent
        before = AlertEvent.objects.count()
        auth_client.get("/api/frameworks/")
        with override_settings(APPLICABLE_COMPLIANCE_FRAMEWORKS=["sox", "iso27001"]):
            auth_client.get("/api/frameworks/")
            auth_client.get("/api/frameworks/sox/")
        assert AlertEvent.objects.count() == before
