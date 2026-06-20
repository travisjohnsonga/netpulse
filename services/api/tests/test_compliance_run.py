"""On-demand compliance runs — apps.compliance.runner + the run endpoints.

The fleet run normally executes in a background thread; tests drive the worker
directly (synchronously) and monkeypatch threading for the endpoint so behaviour
is deterministic.
"""
import pytest
from django.core.cache import cache
from django.utils import timezone

from apps.compliance import runner
from apps.compliance.models import ComplianceTemplate, ComplianceTemplateResult, DeviceComplianceScore
from apps.configbackup.models import DeviceConfig
from apps.devices.models import Device

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()
    yield
    cache.clear()


def _device(host="sw1", ip="10.5.0.1"):
    return Device.objects.create(hostname=host, ip_address=ip,
                                 status=Device.Status.ACTIVE, platform="aos_cx")


def _with_template_result(device, score=80.0):
    tpl = ComplianceTemplate.objects.create(name=f"t-{device.hostname}", template_content="x")
    ComplianceTemplateResult.objects.create(
        device=device, template=tpl, status=ComplianceTemplateResult.Status.COMPLIANT,
        score=score, checked_at=timezone.now())


class TestRunOne:
    def test_run_one_persists_weighted_score(self):
        d = _device()
        _with_template_result(d, 80.0)
        DeviceConfig.objects.create(
            device=d, config_type=DeviceConfig.ConfigType.RUNNING,
            collected_at=timezone.now(), content="hostname sw1", content_hash="h" * 8)
        result = runner.run_one(d)
        row = DeviceComplianceScore.objects.get(device=d)
        assert row.score == result["score"]
        assert row.checked_at is not None


class TestRunDeviceEndpoint:
    def test_post_returns_score(self, auth_client):
        d = _device()
        _with_template_result(d, 90.0)
        resp = auth_client.post(f"/api/compliance/run/{d.id}/")
        assert resp.status_code == 200
        body = resp.json()
        assert body["device_id"] == d.id and "score" in body and "breakdown" in body

    def test_missing_device_404(self, auth_client):
        assert auth_client.post("/api/compliance/run/999999/").status_code == 404

    def test_viewer_forbidden(self, viewer_client):
        d = _device()
        assert viewer_client.post(f"/api/compliance/run/{d.id}/").status_code == 403


class TestRunAllWorker:
    def test_worker_processes_all_active(self):
        a, b = _device("a", "10.5.0.1"), _device("b", "10.5.0.2")
        for d in (a, b):
            _with_template_result(d, 70.0)
        Device.objects.create(hostname="down", ip_address="10.5.0.9",
                              status=Device.Status.INACTIVE)  # excluded
        runner._run_worker(None)   # run synchronously
        st = runner.get_status()
        assert st["total"] == 2 and st["done"] == 2 and st["success"] == 2
        assert st["running"] is False and st["finished_at"]
        assert DeviceComplianceScore.objects.count() == 2

    def test_worker_subset_by_ids(self):
        a, b = _device("a", "10.5.0.1"), _device("b", "10.5.0.2")
        for d in (a, b):
            _with_template_result(d, 70.0)
        runner._run_worker([a.id])
        assert runner.get_status()["total"] == 1
        assert DeviceComplianceScore.objects.filter(device=a).exists()
        assert not DeviceComplianceScore.objects.filter(device=b).exists()

    def test_per_device_error_is_generic(self, monkeypatch):
        d = _device()
        _with_template_result(d, 50.0)

        def _boom(device, role_cache=None):
            raise RuntimeError("secret /srv/path detail")
        monkeypatch.setattr(runner, "run_one", _boom)
        runner._run_worker(None)
        st = runner.get_status()
        assert st["failed"] == 1 and st["success"] == 0
        assert st["errors"][0]["error"] == "compliance run failed"   # no exception text
        assert "secret" not in str(st["errors"])


class TestRunAllEndpoint:
    @pytest.fixture
    def _no_thread(self, monkeypatch):
        """Stop the worker thread from actually running so the lock stays held."""
        class _FakeThread:
            def __init__(self, *a, **k): pass
            def start(self): pass
        monkeypatch.setattr(runner.threading, "Thread", _FakeThread)

    def test_start_then_conflict(self, auth_client, _no_thread):
        _device("a", "10.5.0.1")
        first = auth_client.post("/api/compliance/run-all/")
        assert first.status_code == 202
        assert first.json()["running"] is True and first.json()["total"] == 1
        # Second call while the (fake) run is "in progress" → 409.
        assert auth_client.post("/api/compliance/run-all/").status_code == 409

    def test_status_endpoint(self, auth_client, _no_thread):
        _device("a", "10.5.0.1")
        auth_client.post("/api/compliance/run-all/")
        st = auth_client.get("/api/compliance/run-all/status/").json()
        assert st["running"] is True and st["total"] == 1

    def test_subset_validation(self, auth_client, _no_thread):
        assert auth_client.post("/api/compliance/run-all/",
                                {"device_ids": "nope"}, format="json").status_code == 400

    def test_viewer_forbidden(self, viewer_client, _no_thread):
        assert viewer_client.post("/api/compliance/run-all/").status_code == 403
