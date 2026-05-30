import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command

pytestmark = pytest.mark.django_db
User = get_user_model()


def _env(monkeypatch, **kw):
    for k, v in kw.items():
        monkeypatch.setenv(k, v)


class TestEnsureSuperuser:
    def test_creates_when_absent(self, monkeypatch):
        _env(monkeypatch, DJANGO_SUPERUSER_USERNAME="root1",
             DJANGO_SUPERUSER_PASSWORD="S3cure!pass1", DJANGO_SUPERUSER_EMAIL="r@x.io")
        call_command("ensure_superuser")
        u = User.objects.get(username="root1")
        assert u.is_superuser and u.is_staff and u.role == "admin"
        assert u.check_password("S3cure!pass1")

    def test_idempotent_leaves_existing_untouched(self, monkeypatch):
        u = User.objects.create_superuser(username="root2", email="", password="orig!pass99", role="admin")
        _env(monkeypatch, DJANGO_SUPERUSER_USERNAME="root2", DJANGO_SUPERUSER_PASSWORD="different!pass")
        call_command("ensure_superuser")
        u.refresh_from_db()
        assert u.check_password("orig!pass99")  # unchanged
        assert User.objects.filter(username="root2").count() == 1

    def test_skips_when_env_missing(self, monkeypatch):
        monkeypatch.delenv("DJANGO_SUPERUSER_USERNAME", raising=False)
        monkeypatch.delenv("DJANGO_SUPERUSER_PASSWORD", raising=False)
        call_command("ensure_superuser")
        assert not User.objects.filter(is_superuser=True).exists()
