"""Integration: service checks — create http+tcp, results, summary, run-now."""
import pytest

from apps.checks.models import ServiceCheck

pytestmark = pytest.mark.django_db


class TestCreateChecks:
    def test_create_http_check(self, auth_client):
        resp = auth_client.post(
            "/api/checks/",
            {"name": "API health", "check_type": "http", "host": "127.0.0.1",
             "port": 8000, "config": {"path": "/api/health/", "expected_status": 200}},
            format="json",
        )
        assert resp.status_code == 201, resp.content
        body = resp.json()
        assert body["check_type"] == "http"
        assert body["effective_port"] == 8000

    def test_create_tcp_check_defaults(self, auth_client):
        resp = auth_client.post(
            "/api/checks/",
            {"name": "SSH port", "check_type": "tcp", "host": "10.8.0.1", "port": 22},
            format="json",
        )
        assert resp.status_code == 201, resp.content
        assert resp.json()["effective_port"] == 22

    def test_unauthenticated_rejected(self, api_client):
        assert api_client.get("/api/checks/").status_code == 401


class TestResultsAndSummary:
    def test_summary_shape(self, auth_client):
        ServiceCheck.objects.create(name="c1", check_type="tcp", host="h1",
                                    current_status="up", is_active=True)
        ServiceCheck.objects.create(name="c2", check_type="tcp", host="h2",
                                    current_status="down", is_active=True)
        resp = auth_client.get("/api/checks/summary/")
        assert resp.status_code == 200
        body = resp.json()
        for key in ("up", "down", "degraded", "unknown", "total"):
            assert key in body
        assert body["total"] == 2
        assert body["up"] == 1 and body["down"] == 1

    def test_results_history_shape(self, auth_client):
        check = ServiceCheck.objects.create(name="c", check_type="tcp", host="h")
        resp = auth_client.get(f"/api/checks/{check.pk}/results/?period=24h")
        assert resp.status_code == 200
        body = resp.json()
        assert body["check_id"] == check.pk
        assert body["period"] == "24h"
        assert "summary" in body
        assert body["results"] == []


class TestRunNow:
    @pytest.mark.requires_devices
    def test_run_now_returns_result_structure(self, auth_client):
        # run-now performs real network I/O; gated behind requires_devices.
        check = ServiceCheck.objects.create(
            name="local-api", check_type="http", host="127.0.0.1", port=8000,
            config={"path": "/api/health/", "expected_status": 200},
        )
        resp = auth_client.post(f"/api/checks/{check.pk}/run-now/")
        assert resp.status_code == 200
        body = resp.json()
        # Don't assert up/down (env-dependent) — just the result envelope.
        assert "status" in body
        assert "current_status" in body
        assert "checked_at" in body
