"""Integration: config backup — DeviceConfig listing endpoint (light)."""
import pytest

pytestmark = pytest.mark.django_db


class TestDeviceConfigListing:
    def test_list_requires_auth(self, api_client):
        assert api_client.get("/api/configbackup/configs/").status_code == 401

    def test_list_empty_ok(self, auth_client):
        resp = auth_client.get("/api/configbackup/configs/")
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 0
        assert body["results"] == []

    def test_settings_endpoint_ok(self, auth_client):
        # ConfigBackupSettings singleton-style endpoint.
        resp = auth_client.get("/api/configbackup/config-backup/")
        assert resp.status_code == 200
