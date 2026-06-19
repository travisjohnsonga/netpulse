"""Tests for the platform backup/restore subsystem (models, serializer, API)."""
import pytest

from apps.backup.models import (
    ENCRYPTION_VAULT_PATH,
    S3_VAULT_PATH,
    SCP_VAULT_PATH,
    BackupConfig,
    BackupRecord,
)
from apps.backup.runner import BackupResult

pytestmark = pytest.mark.django_db


# ── models ──────────────────────────────────────────────────────────────────
class TestBackupConfig:
    def test_load_is_singleton(self):
        a = BackupConfig.load()
        b = BackupConfig.load()
        assert a.pk == 1 and b.pk == 1
        assert BackupConfig.objects.count() == 1

    def test_save_forces_pk_1(self):
        cfg = BackupConfig(schedule="daily")
        cfg.save()
        assert cfg.pk == 1

    def test_defaults(self):
        cfg = BackupConfig.load()
        assert cfg.schedule == "disabled"
        assert cfg.retention_days == 30
        assert cfg.include_postgres is True
        assert cfg.include_influxdb is False
        assert cfg.include_openbao is True
        assert cfg.encryption_required is True
        assert cfg.local_path == "/opt/spane/backups"
        assert cfg.destination == "local"


# ── config endpoint ─────────────────────────────────────────────────────────
class TestConfigEndpoint:
    def test_get_config(self, auth_client):
        resp = auth_client.get("/api/backup/config/")
        assert resp.status_code == 200
        body = resp.json()
        assert body["schedule"] == "disabled"
        assert body["encryption_required"] is True

    def test_put_config(self, auth_client, monkeypatch):
        monkeypatch.setattr("apps.credentials.vault.write_secret", lambda p, d: None)
        resp = auth_client.put(
            "/api/backup/config/",
            {"schedule": "daily", "retention_days": 14, "include_influxdb": True},
            format="json")
        assert resp.status_code == 200
        cfg = BackupConfig.load()
        assert cfg.schedule == "daily" and cfg.retention_days == 14
        assert cfg.include_influxdb is True

    def test_serializer_never_returns_secret_values(self, auth_client, monkeypatch):
        # Stash whatever's written so we can prove the response only has *_set flags.
        store = {}
        monkeypatch.setattr("apps.credentials.vault.write_secret",
                            lambda p, d: store.setdefault(p, {}).update(d))
        monkeypatch.setattr("apps.credentials.vault.read_secret",
                            lambda p: store.get(p, {}))
        resp = auth_client.put(
            "/api/backup/config/",
            {"destination": "s3", "s3_bucket": "b", "s3_access_key": "AKIA-SECRET",
             "s3_secret": "shh-secret-value", "encryption_password": "scheduled-pw-1234"},
            format="json")
        assert resp.status_code == 200
        body = resp.json()
        # No secret value of any kind leaks into the response.
        flat = str(body)
        assert "AKIA-SECRET" not in flat
        assert "shh-secret-value" not in flat
        assert "scheduled-pw-1234" not in flat
        assert "s3_access_key" not in body and "s3_secret" not in body
        assert "encryption_password" not in body
        # Presence flags ARE exposed (computed from OpenBao).
        assert body["s3_access_key_set"] is True
        assert body["s3_secret_set"] is True
        assert body["encryption_password_set"] is True
        # And the secrets actually landed in OpenBao (not the DB).
        assert store[S3_VAULT_PATH] == {"access_key": "AKIA-SECRET", "secret_key": "shh-secret-value"}
        assert store[ENCRYPTION_VAULT_PATH] == {"password": "scheduled-pw-1234"}

    def test_presence_flags_false_when_unset(self, auth_client, monkeypatch):
        monkeypatch.setattr("apps.credentials.vault.read_secret", lambda p: {})
        body = auth_client.get("/api/backup/config/").json()
        assert body["scp_password_set"] is False
        assert body["s3_secret_set"] is False
        assert body["encryption_password_set"] is False

    def test_put_non_admin_forbidden(self, viewer_client):
        resp = viewer_client.put("/api/backup/config/", {"schedule": "daily"}, format="json")
        assert resp.status_code == 403


# ── run endpoint (the security point) ───────────────────────────────────────
class TestRunEndpoint:
    def _patch_runner(self, monkeypatch, captured):
        def _fake(**kwargs):
            captured.update(kwargs)
            return BackupResult(
                ok=True, archive_path="/opt/spane/backups/spane-backup-x.enc.tar.gz",
                filename="spane-backup-x.enc.tar.gz", size_bytes=4096,
                duration_seconds=2,
                components={"postgres": kwargs.get("include_postgres")})
        monkeypatch.setattr("apps.backup.views.run_backup", _fake)

    def test_rejects_when_openbao_and_no_password(self, auth_client):
        resp = auth_client.post("/api/backup/run/",
                                {"include_openbao": True}, format="json")
        assert resp.status_code == 400
        assert "password" in resp.json()["error"].lower()

    def test_rejects_when_certs_and_short_password(self, auth_client):
        resp = auth_client.post(
            "/api/backup/run/",
            {"include_certs": True, "include_openbao": False, "include_postgres": False,
             "password": "short"},
            format="json")
        assert resp.status_code == 400
        assert "12" in resp.json()["error"]

    def test_rejects_when_postgres_and_no_password(self, auth_client):
        resp = auth_client.post(
            "/api/backup/run/",
            {"include_postgres": True, "include_openbao": False, "include_certs": False},
            format="json")
        assert resp.status_code == 400

    def test_accepts_with_valid_password_and_never_persists_it(self, auth_client, monkeypatch):
        captured = {}
        self._patch_runner(monkeypatch, captured)
        resp = auth_client.post(
            "/api/backup/run/",
            {"include_openbao": True, "include_postgres": True, "include_certs": True,
             "password": "a-very-strong-passphrase", "password_hint": "vault keepass entry"},
            format="json")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "success"
        assert body["encrypted"] is True
        assert body["encryption_hint"] == "vault keepass entry"
        # Password reached the runner...
        assert captured["password"] == "a-very-strong-passphrase"
        # ...but NEVER the response body or the DB.
        assert "password" not in body
        assert "a-very-strong-passphrase" not in str(body)
        record = BackupRecord.objects.get(pk=body["id"])
        assert "a-very-strong-passphrase" not in str(record.__dict__)
        assert record.encryption_hint == "vault keepass entry"
        assert record.triggered_by == "manual"
        assert record.encrypted is True

    def test_non_sensitive_backup_allowed_without_password(self, auth_client, monkeypatch):
        captured = {}
        self._patch_runner(monkeypatch, captured)
        resp = auth_client.post(
            "/api/backup/run/",
            {"include_postgres": False, "include_openbao": False, "include_certs": False,
             "include_config": True},
            format="json")
        assert resp.status_code == 200
        assert captured["password"] is None
        assert resp.json()["encrypted"] is False

    def test_run_non_admin_forbidden(self, viewer_client):
        resp = viewer_client.post("/api/backup/run/", {"include_config": True}, format="json")
        assert resp.status_code == 403


# ── records ─────────────────────────────────────────────────────────────────
class TestRecords:
    def test_list_and_detail(self, auth_client):
        r = BackupRecord.objects.create(status="success", filename="b.tar.gz", triggered_by="manual")
        resp = auth_client.get("/api/backup/records/")
        assert resp.status_code == 200
        data = resp.json()
        rows = data["results"] if isinstance(data, dict) else data
        assert any(row["filename"] == "b.tar.gz" for row in rows)
        detail = auth_client.get(f"/api/backup/records/{r.id}/")
        assert detail.status_code == 200 and detail.json()["status"] == "success"

    def test_records_ordered_newest_first(self, auth_client):
        BackupRecord.objects.create(status="success", filename="old.tar.gz")
        BackupRecord.objects.create(status="success", filename="new.tar.gz")
        data = auth_client.get("/api/backup/records/").json()
        rows = data["results"] if isinstance(data, dict) else data
        assert rows[0]["filename"] == "new.tar.gz"


# ── download ────────────────────────────────────────────────────────────────
class TestDownload:
    def test_404_for_missing_file(self, auth_client):
        r = BackupRecord.objects.create(status="success", local_path="/opt/spane/backups/nope.tar.gz")
        resp = auth_client.get(f"/api/backup/download/{r.id}/")
        assert resp.status_code == 404

    def test_404_for_unknown_record(self, auth_client):
        assert auth_client.get("/api/backup/download/99999/").status_code == 404

    def test_rejects_path_traversal(self, auth_client, tmp_path):
        # A record whose local_path escapes the configured local_path is rejected.
        cfg = BackupConfig.load()
        cfg.local_path = str(tmp_path)
        cfg.save()
        outside = tmp_path.parent / "secret.txt"
        outside.write_text("nope")
        r = BackupRecord.objects.create(status="success", local_path=str(outside))
        resp = auth_client.get(f"/api/backup/download/{r.id}/")
        assert resp.status_code == 404

    def test_streams_valid_file(self, auth_client, tmp_path):
        cfg = BackupConfig.load()
        cfg.local_path = str(tmp_path)
        cfg.save()
        f = tmp_path / "spane-backup-1.enc.tar.gz"
        f.write_bytes(b"ENCRYPTED-BYTES")
        r = BackupRecord.objects.create(status="success", local_path=str(f),
                                        filename="spane-backup-1.enc.tar.gz")
        resp = auth_client.get(f"/api/backup/download/{r.id}/")
        assert resp.status_code == 200
        assert b"".join(resp.streaming_content) == b"ENCRYPTED-BYTES"


# ── test-connection ─────────────────────────────────────────────────────────
class TestTestConnection:
    def test_local_destination_writable(self, auth_client, tmp_path):
        cfg = BackupConfig.load()
        cfg.destination = "local"
        cfg.local_path = str(tmp_path)
        cfg.save()
        resp = auth_client.post("/api/backup/test-connection/", {}, format="json")
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_scp_missing_host(self, auth_client):
        cfg = BackupConfig.load()
        cfg.destination = "scp"
        cfg.scp_host = ""
        cfg.save()
        resp = auth_client.post("/api/backup/test-connection/", {}, format="json")
        assert resp.status_code == 200
        assert resp.json()["ok"] is False


# ── scheduler ───────────────────────────────────────────────────────────────
class TestScheduler:
    def test_skips_unencrypted_sensitive_backup(self, monkeypatch):
        import datetime

        from apps.backup import scheduler
        cfg = BackupConfig.load()
        cfg.schedule = "daily"
        cfg.schedule_time = datetime.time(2, 0)
        cfg.include_openbao = True
        cfg.encryption_required = True
        cfg.save()
        # No password stored.
        monkeypatch.setattr("apps.backup.scheduler.vault.read_secret", lambda p: {})
        called = {"ran": False}
        monkeypatch.setattr("apps.backup.scheduler.run_backup",
                            lambda **k: called.__setitem__("ran", True))
        now = __import__("django.utils.timezone", fromlist=["now"]).now().replace(hour=2)
        assert scheduler.run_due_backup(now=now) is False
        assert called["ran"] is False
        # No record created for a skipped backup.
        assert not BackupRecord.objects.filter(triggered_by="scheduled").exists()

    def test_runs_when_due_with_stored_password(self, monkeypatch):
        import datetime

        from apps.backup import scheduler
        cfg = BackupConfig.load()
        cfg.schedule = "daily"
        cfg.schedule_time = datetime.time(3, 0)
        cfg.include_openbao = True
        cfg.save()
        monkeypatch.setattr("apps.backup.scheduler.vault.read_secret",
                            lambda p: {"password": "scheduled-strong-pw"})
        captured = {}
        monkeypatch.setattr(
            "apps.backup.scheduler.run_backup",
            lambda **k: (captured.update(k) or BackupResult(
                ok=True, archive_path="/opt/spane/backups/s.enc.tar.gz",
                filename="s.enc.tar.gz", size_bytes=1, duration_seconds=1)))
        now = __import__("django.utils.timezone", fromlist=["now"]).now().replace(hour=3)
        assert scheduler.run_due_backup(now=now) is True
        assert captured["password"] == "scheduled-strong-pw"
        rec = BackupRecord.objects.get(triggered_by="scheduled")
        assert rec.status == "success" and rec.encrypted is True
        # Password never persisted.
        assert "scheduled-strong-pw" not in str(rec.__dict__)

    def test_not_due_when_disabled(self):
        from apps.backup import scheduler
        cfg = BackupConfig.load()
        cfg.schedule = "disabled"
        cfg.save()
        assert scheduler.run_due_backup() is False
