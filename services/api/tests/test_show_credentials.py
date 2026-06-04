"""show_credentials management command: truncation (length-hiding) + HTTPS display."""
from io import StringIO

import pytest
from django.core.management import call_command

from apps.credentials.management.commands import show_credentials as cmd
from apps.credentials.models import CredentialProfile

pytestmark = pytest.mark.django_db


class TestTruncateSecret:
    def test_examples(self):
        t = cmd.truncate_secret
        assert t("") == "(empty)"
        assert t("abc") == "********"               # ≤4 fully masked
        assert t("abcd") == "********"
        assert t("abcde") == "ab********"           # ≤8 first 2 + mask
        assert t("netmagic") == "ne********"
        assert t("abcdefghi") == "abcd********fghi"  # first 4 + last 4
        assert t("abcdefghijklmnop") == "abcd********mnop"

    def test_never_leaks_length_or_full_value(self):
        for s in ("abc", "netmagic", "abcdefghijklmnop"):
            out = cmd.truncate_secret(s)
            assert "len" not in out
            assert s not in out  # full value never appears
            assert "********" in out


def _run(monkeypatch, secret, **opts):
    monkeypatch.setattr(cmd, "read_secret", lambda path: secret)
    out = StringIO()
    call_command("show_credentials", stdout=out, **opts)
    return out.getvalue()


class TestShowCredentials:
    def test_show_secrets_truncates_and_hides_length(self, monkeypatch):
        CredentialProfile.objects.create(name="p", ssh_enabled=True, ssh_username="admin")
        out = _run(monkeypatch, {"ssh_password": "abcdefghijklmnop"}, show_secrets=True)
        assert "abcd********mnop" in out
        assert "abcdefghijklmnop" not in out   # full secret never shown
        assert "len=" not in out               # length hidden everywhere

    def test_default_mode_status_no_length(self, monkeypatch):
        CredentialProfile.objects.create(name="p", ssh_enabled=True, ssh_username="admin")
        out = _run(monkeypatch, {"ssh_password": "abcdefghijklmnop"})
        assert "✅ set" in out
        assert "len=" not in out
        assert "abcdefghijklmnop" not in out

    def test_missing_secret_shows_status(self, monkeypatch):
        CredentialProfile.objects.create(name="p", ssh_enabled=True, ssh_username="admin")
        out = _run(monkeypatch, {})
        assert "❌ missing" in out

    def test_https_credentials_shown_when_enabled(self, monkeypatch):
        CredentialProfile.objects.create(name="api", https_enabled=True,
                                         https_username="apiadmin", https_auth_type="basic")
        out = _run(monkeypatch, {"https_password": "SuperSecretApiPw"}, show_secrets=True)
        assert "HTTPS username: apiadmin" in out
        assert "HTTPS auth type: basic" in out
        assert "HTTPS password: Supe********piPw" in out

    def test_https_api_key_field_name(self, monkeypatch):
        # The vault key is https_api_key (not api_key).
        CredentialProfile.objects.create(name="api", https_enabled=True,
                                         https_username="x", https_auth_type="apikey")
        out = _run(monkeypatch, {"https_api_key": "keykeykeykey"}, show_secrets=True)
        assert "HTTPS API key: keyk********ykey" in out

    def test_https_hidden_when_not_enabled(self, monkeypatch):
        CredentialProfile.objects.create(name="ssh", ssh_enabled=True, ssh_username="admin")
        out = _run(monkeypatch, {"https_password": "shouldnotshow"}, show_secrets=True)
        assert "HTTPS" not in out
