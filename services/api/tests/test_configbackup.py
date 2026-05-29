import pytest

from apps.configbackup.models import ConfigBackupSettings

pytestmark = pytest.mark.django_db


class TestConfigBackupSettings:
    def test_get_creates_singleton_defaults(self, auth_client):
        resp = auth_client.get("/api/settings/config-backup/")
        assert resp.status_code == 200
        body = resp.json()
        assert body["local_enabled"] is True
        assert body["local_path"] == "/opt/netpulse/configs"
        assert body["local_retention_days"] == 90
        assert body["git_enabled"] is False
        assert ConfigBackupSettings.objects.count() == 1

    def test_update_local_settings(self, auth_client):
        resp = auth_client.patch("/api/settings/config-backup/", {
            "local_retention_days": 30, "local_path": "/data/configs",
        }, format="json")
        assert resp.status_code == 200
        assert resp.json()["local_retention_days"] == 30
        # Still one singleton row.
        assert ConfigBackupSettings.objects.count() == 1

    def test_git_credential_goes_to_vault_not_db(self, auth_client):
        resp = auth_client.patch("/api/settings/config-backup/", {
            "git_enabled": True,
            "git_provider": "github",
            "git_repo_url": "https://github.com/org/configs",
            "git_auth_method": "token",
            "git_credential": "ghp_supersecret",
        }, format="json")
        assert resp.status_code == 200
        body = resp.json()
        assert "git_credential" not in body
        assert body["git_vault_path"]  # path recorded
        obj = ConfigBackupSettings.load()
        assert "ghp_supersecret" not in str(obj.__dict__)

    def test_viewer_cannot_update(self, viewer_client):
        resp = viewer_client.patch("/api/settings/config-backup/", {"local_retention_days": 10}, format="json")
        assert resp.status_code == 403

    def test_unauthenticated_rejected(self, api_client):
        assert api_client.get("/api/settings/config-backup/").status_code == 401


class TestGitActions:
    def test_test_git_requires_repo(self, auth_client):
        resp = auth_client.post("/api/settings/config-backup/test-git/", {}, format="json")
        assert resp.status_code == 200
        assert resp.json()["ok"] is False  # no repo configured

    def test_test_git_probes_supplied_url(self, auth_client):
        # Loopback:443 is closed in the test env → fast, ok=False but well-formed.
        resp = auth_client.post("/api/settings/config-backup/test-git/", {
            "git_repo_url": "https://127.0.0.1/org/repo.git",
        }, format="json")
        assert resp.status_code == 200
        assert "message" in resp.json()

    def test_sync_now_disabled(self, auth_client):
        resp = auth_client.post("/api/settings/config-backup/sync-now/")
        assert resp.status_code == 400  # git not enabled

    def test_sync_now_records_request(self, auth_client):
        auth_client.patch("/api/settings/config-backup/", {
            "git_enabled": True, "git_repo_url": "https://github.com/org/configs",
        }, format="json")
        resp = auth_client.post("/api/settings/config-backup/sync-now/")
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        assert ConfigBackupSettings.load().last_sync_at is not None
