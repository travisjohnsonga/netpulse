"""Version + update-check endpoints (no auth). App version is git-app-tag derived
(Option C semver) with an explicit env override — no hidden VERSION file."""
import pytest

pytestmark = pytest.mark.django_db


class TestVersion:
    def test_version_no_auth(self, api_client):
        r = api_client.get("/api/version/")
        assert r.status_code == 200
        body = r.json()
        assert body["version"]  # a non-empty derived version (semver or 0.0.0+sha)
        assert set(body) >= {"version", "commit", "branch", "built_at"}

    def test_check_update_available(self, api_client, monkeypatch):
        from django.core.cache import cache
        from apps.core import version as v
        cache.delete(v._CACHE_KEY)
        # tag-based: a newer app-v* tag is available.
        monkeypatch.setattr(v, "_check_github", lambda: {
            "latest_version": "9.9.9", "update_available": True, "commits_behind": 0,
            "release_notes_url": "https://github.com/x/y/releases/tag/app-v9.9.9"})
        body = api_client.get("/api/version/check/").json()
        assert body["update_available"] is True
        assert body["latest_version"] == "9.9.9"
        assert body["current_version"]  # non-empty
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


class TestSemverComparison:
    def test_semver_tuple_parses_app_tag_and_dev_suffix(self):
        from apps.core.version import _semver_tuple
        assert _semver_tuple("app-v0.5.0") == (0, 5, 0)
        assert _semver_tuple("0.5.0") == (0, 5, 0)
        assert _semver_tuple("0.5.0-3-gabc123") == (0, 5, 0)  # dev suffix dropped
        assert _semver_tuple("0.0.0+deadbeef") == (0, 0, 0)   # build suffix dropped
        assert _semver_tuple("garbage") == (0, 0, 0)

    def test_update_available_only_when_tag_newer(self, monkeypatch, settings):
        from apps.core import version as v
        settings.VERSION = "0.5.0"
        # Picks the highest app-v* tag by semver; newer → update available.
        monkeypatch.setattr(v.requests if hasattr(v, "requests") else v, "_github_headers",
                            lambda: {}, raising=False)

        class _Resp:
            status_code = 200
            def json(self):
                return [{"name": "app-v0.4.0"}, {"name": "app-v0.6.0"},
                        {"name": "v1.5.0"}]  # agent tag must be IGNORED

        import apps.core.version as mod
        monkeypatch.setattr("requests.get", lambda *a, **k: _Resp())
        out = mod._check_github()
        assert out["latest_version"] == "0.6.0"      # highest app-v*, not the agent v1.5.0
        assert out["update_available"] is True

    def test_no_update_when_current_is_latest(self, monkeypatch, settings):
        from apps.core import version as mod
        settings.VERSION = "0.6.0"

        class _Resp:
            status_code = 200
            def json(self):
                return [{"name": "app-v0.6.0"}, {"name": "v9.9.9"}]
        monkeypatch.setattr("requests.get", lambda *a, **k: _Resp())
        out = mod._check_github()
        assert out["latest_version"] == "0.6.0" and out["update_available"] is False


class TestHealthVersionResolution:
    """_netpulse_version() (the source for /api/health/[infrastructure/]) — env
    override → settings.VERSION. No file tier."""

    @pytest.fixture(autouse=True)
    def _clear_env(self, monkeypatch):
        for var in ("SPANE_VERSION", "NETPULSE_VERSION"):
            monkeypatch.delenv(var, raising=False)
        yield

    def test_spane_version_env_wins(self, monkeypatch):
        from apps.core import views
        monkeypatch.setenv("SPANE_VERSION", "v0.2.0")
        assert views._netpulse_version() == "v0.2.0"

    def test_netpulse_version_env_also_honoured(self, monkeypatch):
        from apps.core import views
        monkeypatch.setenv("NETPULSE_VERSION", "0.3.1")
        assert views._netpulse_version() == "0.3.1"

    def test_dev_sentinel_ignored_falls_to_settings(self, monkeypatch, settings):
        from apps.core import views
        settings.VERSION = "0.5.0"
        monkeypatch.setenv("SPANE_VERSION", "dev")  # ignored → settings.VERSION
        assert views._netpulse_version() == "0.5.0"

    def test_falls_back_to_settings_version(self, settings):
        from apps.core import views
        settings.VERSION = "0.7.2"
        assert views._netpulse_version() == "0.7.2"

    def test_infrastructure_health_reports_version(self, auth_client, monkeypatch):
        monkeypatch.setenv("SPANE_VERSION", "v9.9.9")
        resp = auth_client.get("/api/health/infrastructure/")
        assert resp.json()["version"] == "v9.9.9"
