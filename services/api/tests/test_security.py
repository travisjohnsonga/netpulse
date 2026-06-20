import ast
from pathlib import Path

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


# ── Exception-exposure regression (CWE-209 / CodeQL py/stack-trace-exposure) ──
#
# No view may return raw exception detail in an API response. Every except
# handler must funnel the exception through a sanitizer (safe_detail /
# internal_error_response) that logs server-side and returns a static message.
# Complements the broader apps/-wide guard in test_codeql_fixes.py.

_APPS_DIR = Path(__file__).resolve().parent.parent / "apps"
_SANITIZERS = {"safe_detail", "internal_error_response", "log_internal_error"}
_RESPONSE_SINKS = {"Response", "JsonResponse", "HttpResponse"}


def _view_files():
    return [p for p in _APPS_DIR.rglob("views.py")
            if not {"migrations", "__pycache__", "tests"} & set(p.parts)]


def _called_name(call):
    f = call.func
    if isinstance(f, ast.Name):
        return f.id
    if isinstance(f, ast.Attribute):
        return f.attr
    return None


def _exc_escapes(arg_nodes, var):
    """True if ``var`` is referenced in arg_nodes outside a sanitizer call."""
    found = False

    class V(ast.NodeVisitor):
        def visit_Call(self, node):
            if _called_name(node) in _SANITIZERS:
                return  # sanitized subtree — don't descend
            self.generic_visit(node)

        def visit_Name(self, node):
            nonlocal found
            if node.id == var:
                found = True

    v = V()
    for n in arg_nodes:
        v.visit(n)
    return found


def _response_violations(tree):
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler) or not node.name:
            continue
        for sub in ast.walk(node):
            if isinstance(sub, ast.Return) and isinstance(sub.value, ast.Call) \
                    and _called_name(sub.value) in _RESPONSE_SINKS:
                args = list(sub.value.args) + [kw.value for kw in sub.value.keywords]
                if _exc_escapes(args, node.name):
                    out.append(sub.lineno)
    return out


class TestNoExceptionExposure:
    def test_no_str_exception_in_response_ast(self):
        violations = []
        for fp in _view_files():
            for lineno in _response_violations(ast.parse(fp.read_text(), filename=str(fp))):
                violations.append(f"{fp.relative_to(_APPS_DIR.parent)}:{lineno}")
        assert not violations, (
            "Exception details exposed in API responses (CWE-209):\n  "
            + "\n  ".join(violations)
            + "\n\nFix: log the exception (exc_info=True) and return a generic "
            "message via safe_detail()/internal_error_response()."
        )

    def test_guard_detects_planted_leak(self):
        bad = ast.parse("def v():\n try:\n  f()\n except Exception as e:\n"
                        "  return Response({'error': str(e)})\n")
        good = ast.parse("def v():\n try:\n  f()\n except Exception as e:\n"
                         "  return Response({'error': safe_detail(e, logger, 'v')})\n")
        assert _response_violations(bad)
        assert not _response_violations(good)
