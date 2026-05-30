import pytest

pytestmark = pytest.mark.django_db


class TestCVEFeedSettings:
    def test_get_defaults(self, auth_client):
        b = auth_client.get("/api/cve/feed-settings/").json()
        assert b["nvd_enabled"] is True and b["cisa_kev_enabled"] is True
        assert b["cisco_psirt_enabled"] is False and b["paloalto_enabled"] is False
        assert b["has_nvd_api_key"] is False
        assert b["has_psirt_credentials"] is False
        assert b["has_paloalto_api_key"] is False
        # secrets / vault paths never serialized
        assert "nvd_api_key" not in b and "nvd_api_key_vault_path" not in b
        assert "cisco_psirt_client_secret" not in b

    def test_set_nvd_key_flips_flag(self, auth_client):
        resp = auth_client.put("/api/cve/feed-settings/", {"nvd_api_key": "secret-key"}, format="json")
        assert resp.status_code == 200
        assert resp.json()["has_nvd_api_key"] is True
        from apps.cve.models import CVEFeedSettings
        assert CVEFeedSettings.load().nvd_api_key_vault_path == "cve-feeds/nvd"
        # response never echoes the secret
        assert "secret-key" not in resp.content.decode()

    def test_psirt_credentials_atomic(self, auth_client):
        resp = auth_client.put("/api/cve/feed-settings/", {
            "cisco_psirt_enabled": True,
            "cisco_psirt_client_id": "abc", "cisco_psirt_client_secret": "xyz",
        }, format="json")
        assert resp.status_code == 200
        b = resp.json()
        assert b["has_psirt_credentials"] is True and b["cisco_psirt_enabled"] is True
        assert "abc" not in resp.content.decode() and "xyz" not in resp.content.decode()

    def test_paloalto_key(self, auth_client):
        resp = auth_client.put("/api/cve/feed-settings/", {"paloalto_api_key": "pan"}, format="json")
        assert resp.json()["has_paloalto_api_key"] is True

    def test_toggle_without_secret_keeps_flag_false(self, auth_client):
        resp = auth_client.put("/api/cve/feed-settings/", {"nvd_enabled": False}, format="json")
        assert resp.json()["nvd_enabled"] is False
        assert resp.json()["has_nvd_api_key"] is False

    def test_unauthenticated(self, api_client):
        assert api_client.get("/api/cve/feed-settings/").status_code == 401
