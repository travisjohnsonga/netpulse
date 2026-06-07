"""Tests for the EmailSettings (Settings → Integrations → Email) backend."""
import pytest

from apps.integrations import email as email_mod
from apps.integrations.email import PROVIDER_PRESETS
from apps.integrations.models import EmailSettings, SMTP_VAULT_PATH

pytestmark = pytest.mark.django_db


class TestModelAndPresets:
    def test_load_is_singleton(self):
        a = EmailSettings.load()
        b = EmailSettings.load()
        assert a.pk == b.pk and EmailSettings.objects.count() == 1

    def test_presets_have_required_keys(self):
        for key in ("gmail", "m365", "sendgrid", "mailgun", "custom"):
            assert key in PROVIDER_PRESETS
            assert "host" in PROVIDER_PRESETS[key] and "help" in PROVIDER_PRESETS[key]
        assert PROVIDER_PRESETS["sendgrid"]["username"] == "apikey"


class TestEmailSettingsEndpoint:
    def test_get_returns_settings_and_presets(self, auth_client):
        resp = auth_client.get("/api/integrations/email/")
        assert resp.status_code == 200
        body = resp.json()
        assert body["provider"] == "custom" and body["enabled"] is False
        assert "provider_presets" in body and "gmail" in body["provider_presets"]
        assert "password" not in body  # write-only, never returned

    def test_put_updates_settings_and_writes_password(self, auth_client, monkeypatch):
        writes = {}
        monkeypatch.setattr("apps.credentials.vault.write_secret",
                            lambda path, data: writes.update({path: data}))
        resp = auth_client.put("/api/integrations/email/", {
            "provider": "gmail", "host": "smtp.gmail.com", "port": 587,
            "username": "me@gmail.com", "from_email": "me@gmail.com", "enabled": True,
            "password": "app-pass-123",
        }, format="json")
        assert resp.status_code == 200
        cfg = EmailSettings.load()
        assert cfg.host == "smtp.gmail.com" and cfg.enabled is True and cfg.provider == "gmail"
        assert writes.get(SMTP_VAULT_PATH) == {"password": "app-pass-123"}

    def test_put_without_password_does_not_write(self, auth_client, monkeypatch):
        called = {"n": 0}
        monkeypatch.setattr("apps.credentials.vault.write_secret",
                            lambda path, data: called.__setitem__("n", called["n"] + 1))
        resp = auth_client.put("/api/integrations/email/",
                               {"host": "mail.example.com", "enabled": True}, format="json")
        assert resp.status_code == 200
        assert called["n"] == 0  # no password supplied → OpenBao untouched

    def test_test_endpoint_success(self, auth_client, monkeypatch):
        monkeypatch.setattr(email_mod, "send_test_email", lambda to: (True, ""))
        # patch the symbol imported into views too
        monkeypatch.setattr("apps.integrations.views.send_test_email", lambda to: (True, ""))
        resp = auth_client.post("/api/integrations/email/test/", {"to": "ops@example.com"}, format="json")
        assert resp.status_code == 200 and resp.json() == {"sent": True}

    def test_test_endpoint_requires_recipient(self, auth_client):
        resp = auth_client.post("/api/integrations/email/test/", {}, format="json")
        assert resp.status_code == 400

    def test_test_endpoint_failure_surfaces_error(self, auth_client, monkeypatch):
        monkeypatch.setattr("apps.integrations.views.send_test_email",
                            lambda to: (False, "Connection refused"))
        resp = auth_client.post("/api/integrations/email/test/", {"to": "x@example.com"}, format="json")
        assert resp.status_code == 502 and resp.json()["error"] == "Connection refused"


class TestSendAlertEmail:
    def test_skips_when_disabled(self):
        EmailSettings.objects.create(host="smtp.example.com", enabled=False)
        assert email_mod.send_alert_email(["a@b.com"], "s", "b") is False

    def test_sends_when_enabled(self, monkeypatch):
        EmailSettings.objects.create(host="smtp.example.com", enabled=True, from_email="np@x.com")
        sent = {}
        def fake_send_mail(**kwargs):
            sent.update(kwargs)
            return 1
        monkeypatch.setattr("django.core.mail.send_mail", fake_send_mail)
        monkeypatch.setattr(email_mod, "get_smtp_password", lambda: "pw")
        ok = email_mod.send_alert_email(["a@b.com"], "Subj", "Body")
        assert ok is True and sent["recipient_list"] == ["a@b.com"]
        assert sent["subject"] == "Subj"
