import pytest

from apps.compliance.engine import (
    ComplianceEngine,
    get_templates_for_device,
    run_compliance_for_device,
)
from apps.compliance.models import (
    ComplianceTemplate,
    ComplianceTemplateResult,
    DeviceComplianceOverride,
)
from apps.configbackup.models import DeviceConfig
from apps.devices.models import Device, DeviceRole, Site

pytestmark = pytest.mark.django_db


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def device():
    return Device.objects.create(
        hostname="sw1", ip_address="10.1.0.1", management_ip="10.1.0.1",
        platform="aos_cx")


@pytest.fixture
def ntp_template():
    return ComplianceTemplate.objects.create(
        name="NTP Policy",
        template_content="ntp server {{ ntp_server_1 }}\nntp server {{ ntp_server_2 }}\n",
        variables={"ntp_server_1": "10.0.0.1", "ntp_server_2": "10.0.0.2"},
        enabled=True,
    )


def _save_config(device, content):
    from django.utils import timezone
    return DeviceConfig.objects.create(
        device=device, config_type=DeviceConfig.ConfigType.RUNNING,
        collected_at=timezone.now(), content=content, content_hash="x" * 8)


# ── Rendering ─────────────────────────────────────────────────────────────────

class TestRender:
    def test_render_merges_vars_and_device_context(self, device, ntp_template):
        out = ComplianceEngine().render_template(ntp_template, device)
        assert "ntp server 10.0.0.1" in out
        assert "ntp server 10.0.0.2" in out

    def test_overrides_win(self, device, ntp_template):
        out = ComplianceEngine().render_template(ntp_template, device, {"ntp_server_1": "9.9.9.9"})
        assert "ntp server 9.9.9.9" in out

    def test_device_context_available(self, device):
        t = ComplianceTemplate.objects.create(
            name="ctx", template_content="hostname {{ device.hostname }}", variables={})
        assert ComplianceEngine().render_template(t, device) == "hostname sw1"


# ── Diff engine ───────────────────────────────────────────────────────────────

class TestCompare:
    def test_all_present_no_findings(self):
        eng = ComplianceEngine()
        expected = "ntp server 10.0.0.1\nntp server 10.0.0.2\n"
        actual = "!\nntp server 10.0.0.1\nntp server 10.0.0.2\nhostname x\n"
        assert eng.compare_configs(expected, actual) == []

    def test_missing(self):
        eng = ComplianceEngine()
        findings = eng.compare_configs("logging 10.0.0.5\n", "hostname x\n")
        assert len(findings) == 1
        assert findings[0]["type"] == "MISSING" and findings[0]["actual"] is None

    def test_drift(self):
        eng = ComplianceEngine()
        findings = eng.compare_configs("ntp server 10.0.0.1\n", "ntp server 9.9.9.9\n")
        assert len(findings) == 1
        f = findings[0]
        assert f["type"] == "DRIFT" and f["actual"] == "ntp server 9.9.9.9"

    def test_comments_and_blanks_ignored(self):
        eng = ComplianceEngine()
        assert eng.compare_configs("!\n\n", "anything\n") == []

    def test_find_drift_needs_two_word_prefix(self):
        eng = ComplianceEngine()
        # single-token expected line → no drift detection, reported MISSING
        findings = eng.compare_configs("end\n", "endx\n")
        assert findings[0]["type"] == "MISSING"

    def test_generate_remediation(self):
        eng = ComplianceEngine()
        findings = [
            {"type": "MISSING", "expected": "ntp server 10.0.0.1", "actual": None, "line": "ntp server 10.0.0.1"},
            {"type": "DRIFT", "expected": "logging 10.0.0.5", "actual": "logging 1.1.1.1", "line": "logging 10.0.0.5"},
        ]
        rem = eng.generate_remediation(findings)
        assert rem == "ntp server 10.0.0.1\nno logging 1.1.1.1\nlogging 10.0.0.5"


# ── check_device ──────────────────────────────────────────────────────────────

class TestCheckDevice:
    def test_compliant_100(self, device, ntp_template):
        cfg = "ntp server 10.0.0.1\nntp server 10.0.0.2\n"
        r = ComplianceEngine().check_device(device, ntp_template, config_text=cfg)
        assert r.status == "compliant" and r.score == 100.0
        assert r.missing_count == 0 and r.drift_count == 0

    def test_non_compliant_counts(self, device):
        # ntp line drifts (same prefix, diff value); logging line is fully missing.
        t = ComplianceTemplate.objects.create(
            name="mix", enabled=True,
            template_content="ntp server {{ a }}\nlogging {{ b }}\n",
            variables={"a": "10.0.0.1", "b": "10.0.0.5"})
        cfg = "ntp server 9.9.9.9\n"
        r = ComplianceEngine().check_device(device, t, config_text=cfg)
        assert r.status == "non_compliant"
        assert r.drift_count == 1 and r.missing_count == 1
        assert 0 <= r.score < 100

    def test_uses_latest_backup_when_no_text(self, device, ntp_template):
        _save_config(device, "ntp server 10.0.0.1\nntp server 10.0.0.2\n")
        r = ComplianceEngine().check_device(device, ntp_template)
        assert r.status == "compliant"

    def test_no_backup_is_error(self, device, ntp_template):
        r = ComplianceEngine().check_device(device, ntp_template)
        assert r.status == "error" and r.score is None

    def test_device_override_applied(self, device, ntp_template):
        DeviceComplianceOverride.objects.create(
            device=device, template=ntp_template, variables={"ntp_server_1": "8.8.8.8"})
        cfg = "ntp server 8.8.8.8\nntp server 10.0.0.2\n"
        r = ComplianceEngine().check_device(device, ntp_template, config_text=cfg)
        assert r.status == "compliant"


# ── Template selection ────────────────────────────────────────────────────────

class TestTemplateSelection:
    def test_platform_and_global_match_role_excludes(self, device):
        role = DeviceRole.objects.create(name="Core")
        other_role = DeviceRole.objects.create(name="Edge")
        site = Site.objects.create(name="DC1")
        device.role = role; device.site = site; device.save()

        glob = ComplianceTemplate.objects.create(name="g", template_content="x", enabled=True)
        plat = ComplianceTemplate.objects.create(name="p", template_content="x", platform="aos_cx", enabled=True)
        role_t = ComplianceTemplate.objects.create(name="r", template_content="x", role=role, enabled=True)
        ComplianceTemplate.objects.create(name="wrong-role", template_content="x", role=other_role, enabled=True)
        ComplianceTemplate.objects.create(name="wrong-plat", template_content="x", platform="ios_xe", enabled=True)
        ComplianceTemplate.objects.create(name="disabled", template_content="x", enabled=False)

        got = get_templates_for_device(device)
        assert set(t.id for t in got) == {glob.id, plat.id, role_t.id}
        # ordered by specificity: role first, then platform, then global
        assert got[0].id == role_t.id and got[-1].id == glob.id


# ── Orchestration + endpoints ─────────────────────────────────────────────────

class TestRunAndEndpoints:
    def test_run_saves_results(self, device, ntp_template):
        cfg = _save_config(device, "ntp server 10.0.0.1\nntp server 10.0.0.2\n")
        results = run_compliance_for_device(device, config_snapshot=cfg)
        assert len(results) == 1
        assert ComplianceTemplateResult.objects.filter(device=device).count() == 1
        assert results[0].config_snapshot_id == cfg.id

    def test_template_crud(self, auth_client):
        resp = auth_client.post("/api/compliance/templates/", {
            "name": "T1", "template_content": "ntp server {{ s }}", "platform": "aos_cx",
            "variables": {"s": "1.1.1.1"},
        }, format="json")
        assert resp.status_code == 201, resp.content
        tid = resp.json()["id"]
        assert auth_client.get("/api/compliance/templates/").json()["count"] >= 1
        assert auth_client.delete(f"/api/compliance/templates/{tid}/").status_code == 204

    def test_template_invalid_does_not_crash_preview(self, auth_client, device, ntp_template):
        resp = auth_client.post(f"/api/compliance/templates/{ntp_template.id}/preview/",
                                {"device_id": device.id}, format="json")
        assert resp.status_code == 200
        assert "ntp server 10.0.0.1" in resp.json()["rendered"]

    def test_preview_bad_device(self, auth_client, ntp_template):
        resp = auth_client.post(f"/api/compliance/templates/{ntp_template.id}/preview/",
                                {"device_id": 999999}, format="json")
        assert resp.status_code == 400

    def test_check_endpoint_device(self, auth_client, device, ntp_template):
        _save_config(device, "ntp server 10.0.0.1\nntp server 10.0.0.2\n")
        resp = auth_client.post("/api/compliance/check/", {"device_id": device.id}, format="json")
        assert resp.status_code == 200
        body = resp.json()
        assert body["checked"] == 1 and body["compliant"] == 1

    def test_device_compliance_endpoint(self, auth_client, device, ntp_template):
        cfg = _save_config(device, "ntp server 10.0.0.1\n")  # 1 missing
        run_compliance_for_device(device, config_snapshot=cfg)
        resp = auth_client.get(f"/api/devices/{device.id}/compliance/")
        assert resp.status_code == 200
        body = resp.json()
        assert body["overall_score"] is not None
        assert len(body["results"]) == 1
        assert body["results"][0]["template_name"] == "NTP Policy"

    def test_template_results_filter(self, auth_client, device, ntp_template):
        cfg = _save_config(device, "ntp server 10.0.0.1\nntp server 10.0.0.2\n")
        run_compliance_for_device(device, config_snapshot=cfg)
        resp = auth_client.get(f"/api/compliance/template-results/?device={device.id}&status=compliant")
        assert resp.status_code == 200
        assert resp.json()["count"] == 1

    def test_seed_idempotent(self):
        from django.core.management import call_command
        call_command("seed_compliance_templates")
        n = ComplianceTemplate.objects.count()
        assert n == 2
        assert ComplianceTemplate.objects.filter(enabled=True).count() == 0
        call_command("seed_compliance_templates")
        assert ComplianceTemplate.objects.count() == n
