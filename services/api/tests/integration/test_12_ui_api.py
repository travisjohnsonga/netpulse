"""Integration: read endpoints the React UI depends on return 200 for auth."""
import pytest

pytestmark = pytest.mark.django_db

UI_READ_ENDPOINTS = [
    "/api/devices/",
    "/api/devices/sites/",
    "/api/sites/",
    "/api/checks/",
    "/api/checks/summary/",
    "/api/alerting/teams/",
    "/api/alerts/rules/",
    "/api/credentials/",
    "/api/settings/system/",
]


class TestUIReadEndpoints:
    @pytest.mark.parametrize("path", UI_READ_ENDPOINTS)
    def test_authenticated_get_200(self, auth_client, path):
        resp = auth_client.get(path)
        assert resp.status_code == 200, f"{path} -> {resp.status_code}: {resp.content[:200]}"

    def test_system_settings_exposes_config_push_flag(self, auth_client):
        body = auth_client.get("/api/settings/system/").json()
        assert "allow_config_push" in body
        assert isinstance(body["allow_config_push"], bool)
