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
        # Seeded admin must be forced to change the default password on first login.
        assert u.must_change_password is True

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

    def test_invalid_env_email_defaults_to_valid(self, monkeypatch):
        _env(monkeypatch, DJANGO_SUPERUSER_USERNAME="root3",
             DJANGO_SUPERUSER_PASSWORD="S3cure!pass1", DJANGO_SUPERUSER_EMAIL="admin@netpulse")
        call_command("ensure_superuser")
        assert User.objects.get(username="root3").email == "admin@netpulse.local"

    def test_repairs_existing_invalid_email(self, monkeypatch):
        u = User.objects.create_superuser(username="root4", email="x", password="orig!pass99", role="admin")
        u.email = "admin@netpulse"  # bypass validation (no full_clean on save)
        u.save(update_fields=["email"])
        _env(monkeypatch, DJANGO_SUPERUSER_USERNAME="root4", DJANGO_SUPERUSER_PASSWORD="whatever!99")
        call_command("ensure_superuser")
        u.refresh_from_db()
        assert u.email == "admin@netpulse.local"

    def test_leaves_valid_and_blank_email_alone(self, monkeypatch):
        good = User.objects.create_superuser(username="root5", email="ops@example.com", password="p!ass1234", role="admin")
        blank = User.objects.create_superuser(username="root6", email="", password="p!ass1234", role="admin")
        for name in ("root5", "root6"):
            _env(monkeypatch, DJANGO_SUPERUSER_USERNAME=name, DJANGO_SUPERUSER_PASSWORD="x")
            call_command("ensure_superuser")
        good.refresh_from_db(); blank.refresh_from_db()
        assert good.email == "ops@example.com" and blank.email == ""
