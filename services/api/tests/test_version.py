"""Version + update-check endpoints (no auth)."""
import pytest

pytestmark = pytest.mark.django_db


class TestVersion:
    def test_version_no_auth(self, api_client):
        r = api_client.get("/api/version/")
        assert r.status_code == 200
        body = r.json()
        assert body["version"].startswith("1.0.")
        assert set(body) >= {"version", "commit", "branch", "built_at"}

    def test_check_update_available(self, api_client, monkeypatch):
        from django.core.cache import cache
        from apps.core import version as v
        cache.delete(v._CACHE_KEY)
        monkeypatch.setattr(v, "_check_github", lambda: {
            "latest_commit": "abc1234", "update_available": True,
            "commits_behind": 3, "latest_version": "1.0.999"})
        body = api_client.get("/api/version/check/").json()
        assert body["update_available"] is True
        assert body["commits_behind"] == 3
        assert body["latest_commit"] == "abc1234"
        assert body["current_version"].startswith("1.0.")
        assert "release_notes_url" in body
        cache.delete(v._CACHE_KEY)

    def test_check_result_cached(self, api_client, monkeypatch):
        from django.core.cache import cache
        from apps.core import version as v
        cache.delete(v._CACHE_KEY)
        calls = {"n": 0}

        def fake():
            calls["n"] += 1
            return {"update_available": False}
        monkeypatch.setattr(v, "_check_github", fake)
        api_client.get("/api/version/check/")
        api_client.get("/api/version/check/")
        assert calls["n"] == 1  # second call served from cache
        cache.delete(v._CACHE_KEY)

    def test_check_disabled(self, api_client, settings, monkeypatch):
        settings.VERSION_CHECK_ENABLED = False
        from django.core.cache import cache
        from apps.core import version as v
        cache.delete(v._CACHE_KEY)
        monkeypatch.setattr(v, "_check_github",
                            lambda: (_ for _ in ()).throw(AssertionError("must not call")))
        body = api_client.get("/api/version/check/").json()
        assert body["update_available"] is False and body["checked"] is False

    def test_check_transient_failure_no_error(self, api_client, monkeypatch):
        from django.core.cache import cache
        from apps.core import version as v
        cache.delete(v._CACHE_KEY)
        monkeypatch.setattr(v, "_check_github", lambda: None)  # GitHub unreachable
        r = api_client.get("/api/version/check/")
        assert r.status_code == 200
        assert r.json()["update_available"] is False  # no badge, no error
        cache.delete(v._CACHE_KEY)


class TestHealthVersionResolution:
    """_netpulse_version() (the source for /api/health/[infrastructure/])."""

    @pytest.fixture(autouse=True)
    def _clear_env(self, monkeypatch):
        for var in ("SPANE_VERSION", "NETPULSE_VERSION"):
            monkeypatch.delenv(var, raising=False)
        from apps.core import views
        # Point the VERSION-file lookup at a path that doesn't exist by default.
        monkeypatch.setattr(views, "_VERSION_FILE", "/nonexistent/VERSION")
        yield

    def test_spane_version_env_wins(self, monkeypatch):
        from apps.core import views
        monkeypatch.setenv("SPANE_VERSION", "v0.2.0")
        assert views._netpulse_version() == "v0.2.0"

    def test_dev_sentinel_ignored_falls_to_settings(self, monkeypatch):
        from apps.core import views
        monkeypatch.setenv("SPANE_VERSION", "dev")
        # 'dev' skipped → settings.VERSION (1.0.<count>).
        assert views._netpulse_version().startswith("1.0.")

    def test_version_file_used(self, monkeypatch, tmp_path):
        from apps.core import views
        vf = tmp_path / "VERSION"
        vf.write_text("0.1.0\n")
        monkeypatch.setattr(views, "_VERSION_FILE", str(vf))
        assert views._netpulse_version() == "0.1.0"

    def test_falls_back_to_settings_version(self):
        from apps.core import views
        assert views._netpulse_version().startswith("1.0.")

    @pytest.mark.django_db
    def test_infrastructure_health_reports_version(self, auth_client, monkeypatch):
        monkeypatch.setenv("SPANE_VERSION", "v9.9.9")
        resp = auth_client.get("/api/health/infrastructure/")
        assert resp.json()["version"] == "v9.9.9"
