"""
Weighted per-device compliance score (apps.compliance.device_score) and the
enhanced GET /api/devices/{id}/compliance/ endpoint.
"""
import pytest
from django.utils import timezone

from apps.compliance import device_score
from apps.compliance.models import (
    ComplianceTemplate,
    ComplianceTemplateResult,
    InterfaceComplianceRule,
    InterfaceComplianceResult,
)
from apps.configbackup.models import DeviceConfig
from apps.devices.models import Device

pytestmark = pytest.mark.django_db


@pytest.fixture
def device():
    return Device.objects.create(
        hostname="sw1", ip_address="10.1.0.1", management_ip="10.1.0.1", platform="aos_cx")


def _save_config(device, content):
    return DeviceConfig.objects.create(
        device=device, config_type=DeviceConfig.ConfigType.RUNNING,
        collected_at=timezone.now(), content=content, content_hash="x" * 8)


def _template_result(device, score):
    tpl = ComplianceTemplate.objects.create(name=f"tpl-{score}", template_content="x", variables={})
    return ComplianceTemplateResult.objects.create(
        device=device, template=tpl,
        status=ComplianceTemplateResult.Status.COMPLIANT, score=score)


def _iface_result(device, iface, checks, passed):
    rule = InterfaceComplianceRule.objects.create(
        name=f"rule-{iface}", trigger="manual", trigger_value=f"{device.hostname}:{iface}")
    return InterfaceComplianceResult.objects.create(
        rule=rule, device=device, interface=iface, neighbor="ap-1",
        passed=passed, findings=checks, checks_total=len(checks), checked_at=timezone.now())


# ── pure helpers ─────────────────────────────────────────────────────────────

class TestHelpers:
    @pytest.mark.parametrize("score,grade", [
        (100, "A"), (90, "A"), (89.9, "B"), (80, "B"), (70, "C"), (60, "D"), (59, "F"), (None, "N/A"),
    ])
    def test_score_to_grade(self, score, grade):
        assert device_score.score_to_grade(score) == grade

    def test_suggested_fix_aos_cx_spanning_tree(self):
        fix = device_score.suggested_fix("aos_cx", "spanning-tree", "1/1/26")
        assert "interface 1/1/26" in fix
        assert "bpdu-guard" in fix

    def test_suggested_fix_leaves_unknown_placeholder(self):
        fix = device_score.suggested_fix("aos_cx", "voice vlan", "1/1/5")
        assert "{voice_vlan}" in fix      # unfilled placeholder preserved

    def test_suggested_fix_none_for_unknown_platform(self):
        assert device_score.suggested_fix("junos", "spanning-tree", "ge-0/0/1") == ""


# ── weighted scoring ─────────────────────────────────────────────────────────

class TestWeightedScore:
    def test_template_only_passes_through(self, device):
        _template_result(device, 80)
        data = device_score.calculate_device_compliance_score(device)
        assert data["score"] == 80
        assert data["grade"] == "B"
        assert [b["name"] for b in data["breakdown"]] == ["Template Compliance"]

    def test_template_plus_interface_weighted_and_renormalised(self, device):
        # Template 100, interface 50% pass → 100*0.5 + 50*0.3 = 65 over weight 0.8 = 81.25 → 81.2
        _template_result(device, 100)
        _save_config(device, "interface 1/1/1\n    no shutdown\n")
        _iface_result(device, "1/1/1",
                      [{"type": "config_contains", "value": "spanning-tree", "passed": False}], passed=False)
        _iface_result(device, "1/1/2",
                      [{"type": "config_contains", "value": "spanning-tree", "passed": True}], passed=True)
        data = device_score.calculate_device_compliance_score(device)
        assert data["score"] == pytest.approx(81.2, abs=0.1)
        names = {b["name"] for b in data["breakdown"]}
        assert names == {"Template Compliance", "Interface Rules"}
        iface = next(b for b in data["breakdown"] if b["name"] == "Interface Rules")
        assert iface["passing"] == 1 and iface["total"] == 2

    def test_no_data_is_none(self, device):
        data = device_score.calculate_device_compliance_score(device)
        assert data["score"] is None
        assert data["grade"] == "N/A"
        assert data["breakdown"] == []

    def test_interface_findings_include_config_and_fix(self, device):
        _save_config(device, "interface 1/1/26\n    description AP\n    no shutdown\n    vlan access 20\n")
        _iface_result(device, "1/1/26",
                      [{"type": "config_contains", "value": "spanning-tree",
                        "description": "STP portfast", "passed": False}], passed=False)
        findings = device_score.get_interface_rule_findings(device)
        assert len(findings) == 1
        f = findings[0]
        assert "interface 1/1/26" in f["interface_config"]
        assert "vlan access 20" in f["interface_config"]
        assert "bpdu-guard" in f["suggested_fix"]
        assert f["passed"] is False


# ── endpoint ─────────────────────────────────────────────────────────────────

class TestComplianceEndpoint:
    def test_endpoint_returns_score_grade_breakdown_and_backcompat(self, device, auth_client):
        _template_result(device, 90)
        resp = auth_client.get(f"/api/devices/{device.id}/compliance/")
        assert resp.status_code == 200
        body = resp.json()
        # new keys
        assert body["score"] == 90
        assert body["grade"] == "A"
        assert isinstance(body["breakdown"], list) and body["breakdown"]
        assert "interface_rule_findings" in body
        assert "role_consistency_findings" in body
        # back-compat keys still present
        assert body["overall_score"] == 90
        assert isinstance(body["results"], list)


# ── persisted weighted score (DeviceComplianceScore) ─────────────────────────

class TestStoredWeightedScore:
    def test_run_and_store_upserts_weighted_score(self, device):
        from apps.compliance.models import DeviceComplianceScore
        _template_result(device, 40.0)   # template-only would show 40
        result = device_score.run_and_store_compliance(device)

        row = DeviceComplianceScore.objects.get(device=device)
        assert row.score == result["score"]
        assert row.grade == result["grade"]
        assert row.template_score == 40.0
        assert row.checked_at is not None

        # Idempotent: re-running updates the same row, not a second one.
        _template_result(device, 90.0)
        updated = device_score.run_and_store_compliance(device)
        assert DeviceComplianceScore.objects.filter(device=device).count() == 1
        row.refresh_from_db()
        assert row.template_score == updated["template_score"]   # avg(40,90)=65
        assert row.score == updated["score"]

    def test_device_list_annotates_weighted_not_template_score(self, db):
        """The device list subquery reads DeviceComplianceScore.score (weighted),
        which can differ from the template-only ComplianceTemplateResult.score."""
        from django.db.models import FloatField, OuterRef, Subquery

        from apps.compliance.models import DeviceComplianceScore
        dev = Device.objects.create(hostname="sw9", ip_address="10.9.9.9", platform="aos_cx")
        _template_result(dev, 33.0)                       # template-only number
        DeviceComplianceScore.objects.create(device=dev, score=72.0, grade="C")

        latest = (DeviceComplianceScore.objects
                  .filter(device=OuterRef("pk")).values("score")[:1])
        annotated = (Device.objects.filter(pk=dev.pk)
                     .annotate(compliance_score=Subquery(latest, output_field=FloatField()))
                     .first())
        assert annotated.compliance_score == 72.0         # weighted, not 33
