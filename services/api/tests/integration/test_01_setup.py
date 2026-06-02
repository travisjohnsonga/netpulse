"""Integration: platform setup — health, infrastructure, auth, anonymous reject."""
import pytest
from django.core.cache import cache
from rest_framework.throttling import SimpleRateThrottle

pytestmark = pytest.mark.django_db


class TestHealth:
    def test_health_returns_ok(self, api_client):
        # /api/health/ is AllowAny and reports DB connectivity.
        resp = api_client.get("/api/health/")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] in ("ok", "degraded")
        assert body["db"] is True  # SQLite test DB is always reachable.
        # Documented health keys.
        for key in ("status", "db", "setup_complete", "openbao", "ssl_cert_days_remaining"):
            assert key in body

    def test_infrastructure_health_shape(self, api_client):
        resp = api_client.get("/api/health/infrastructure/")
        assert resp.status_code == 200
        services = resp.json()["services"]
        # Backends are unreachable in the test env, but the keys must exist.
        for svc in ("postgres", "valkey", "nats", "influxdb", "opensearch"):
            assert svc in services
        # Postgres maps to the in-memory DB connection → reachable.
        assert services["postgres"] is True


class TestAuth:
    def test_token_returns_access_and_refresh(self, user, api_client):
        resp = api_client.post(
            "/api/auth/token/",
            {"username": "testuser", "password": "testpass123"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "access" in body
        assert "refresh" in body

    def test_bad_credentials_rejected(self, user, api_client):
        resp = api_client.post(
            "/api/auth/token/",
            {"username": "testuser", "password": "wrong"},
        )
        assert resp.status_code == 401

    def test_unauthenticated_devices_list_401(self, api_client):
        resp = api_client.get("/api/devices/")
        assert resp.status_code == 401

    def test_authenticated_devices_list_200(self, auth_client):
        resp = auth_client.get("/api/devices/")
        assert resp.status_code == 200


class TestAuthRateLimiting:
    """
    The `auth` throttle rate is disabled in config.settings.test
    (DEFAULT_THROTTLE_RATES["auth"] = None). We re-enable a tiny rate to prove
    the ScopedRateThrottle wiring is live, mirroring tests/test_rbac.py.
    """

    def test_token_endpoint_is_rate_limited(self, api_client, monkeypatch):
        cache.clear()
        monkeypatch.setattr(SimpleRateThrottle, "THROTTLE_RATES", {"auth": "2/min"})
        try:
            codes = [
                api_client.post(
                    "/api/auth/token/", {"username": "x", "password": "y"}
                ).status_code
                for _ in range(4)
            ]
            assert 429 in codes, f"expected a 429 after the limit, got {codes}"
        finally:
            cache.clear()
