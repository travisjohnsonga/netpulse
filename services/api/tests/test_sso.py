"""
SSO Stage 1 — backend models + Google OAuth2 (apps.sso).

Covers the SSOProvider model, public/admin API, OpenBao secret handling, the
dynamic-credential backend mixin, the custom pipeline steps, and the JWT bridge.
"""
import pytest
from social_core.exceptions import AuthForbidden

from apps.sso import pipeline
from apps.sso.backends import _DBCredentialsMixin
from apps.sso.models import SSOProvider
from apps.sso.views import get_tokens_for_user

pytestmark = pytest.mark.django_db


# ── fake OpenBao (vault is a no-op in tests) ────────────────────────────────────

@pytest.fixture
def fake_vault(monkeypatch):
    store: dict[str, dict] = {}

    def write(path, data):
        store.setdefault(path, {}).update({k: v for k, v in data.items() if v})

    def read(path):
        return dict(store.get(path, {}))

    monkeypatch.setattr("apps.credentials.vault.write_secret", write)
    monkeypatch.setattr("apps.credentials.vault.read_secret", read)
    return store


# ── model ───────────────────────────────────────────────────────────────────────

class TestSSOProviderModel:
    def test_default_vault_path(self):
        p = SSOProvider.objects.create(name="G", provider="google-oauth2")
        assert p.default_vault_path() == f"secret/sso/{p.pk}/credentials"

    def test_single_default_enforced(self):
        a = SSOProvider.objects.create(name="A", provider="google-oauth2", is_default=True)
        b = SSOProvider.objects.create(name="B", provider="github", is_default=True)
        a.refresh_from_db()
        assert b.is_default is True and a.is_default is False


# ── public + admin API ──────────────────────────────────────────────────────────

class TestSSOProviderAPI:
    def test_public_list_no_auth(self, api_client):
        SSOProvider.objects.create(name="Google", provider="google-oauth2", is_enabled=True)
        SSOProvider.objects.create(name="Off", provider="github", is_enabled=False)
        resp = api_client.get("/api/sso/providers/")
        assert resp.status_code == 200
        data = resp.json()
        rows = data["results"] if isinstance(data, dict) and "results" in data else data
        names = [r["name"] for r in rows]
        assert names == ["Google"]                       # only enabled
        assert rows[0]["login_url"] == "/auth/login/google-oauth2/"
        assert "client_id" not in rows[0] and "client_secret" not in rows[0]

    def test_admin_create_stores_secret_in_openbao(self, auth_client, fake_vault):
        resp = auth_client.post("/api/sso/providers/", {
            "name": "Company Google", "provider": "google-oauth2",
            "client_id": "abc.apps.googleusercontent.com",
            "client_secret": "topsecret", "allowed_domains": ["company.com"],
        }, format="json")
        assert resp.status_code == 201, resp.content
        body = resp.json()
        assert body["has_secret"] is True
        assert "client_secret" not in body                # write-only
        p = SSOProvider.objects.get(id=body["id"])
        # Secret in OpenBao, never on the model/DB row.
        assert fake_vault[p.vault_path]["client_secret"] == "topsecret"
        assert not hasattr(p, "client_secret")

    def test_admin_required_for_writes(self, viewer_client):
        resp = viewer_client.post("/api/sso/providers/", {
            "name": "x", "provider": "google-oauth2"}, format="json")
        assert resp.status_code == 403

    def test_test_action_reports_missing_secret(self, auth_client, fake_vault):
        p = SSOProvider.objects.create(name="g", provider="google-oauth2", client_id="cid")
        p.vault_path = p.default_vault_path()
        p.save(update_fields=["vault_path"])
        resp = auth_client.post(f"/api/sso/providers/{p.id}/test/")
        assert resp.status_code == 200
        body = resp.json()
        assert body["valid"] is False and "client_secret" in body["error"]

    def test_test_action_valid_when_configured(self, auth_client, fake_vault):
        p = SSOProvider.objects.create(name="g", provider="google-oauth2", client_id="cid")
        p.vault_path = p.default_vault_path()
        p.save(update_fields=["vault_path"])
        fake_vault[p.vault_path] = {"client_secret": "s"}
        resp = auth_client.post(f"/api/sso/providers/{p.id}/test/")
        assert resp.json() == {"valid": True, "error": None}


# ── dynamic-credential backend ──────────────────────────────────────────────────

class _StaticBase:
    name = "google-oauth2"

    def get_key_and_secret(self):
        return ("static-key", "static-secret")


class _FakeBackend(_DBCredentialsMixin, _StaticBase):
    pass


class TestDynamicBackend:
    def test_prefers_db_and_openbao(self, fake_vault):
        p = SSOProvider.objects.create(
            name="g", provider="google-oauth2", client_id="db-key", is_enabled=True)
        p.vault_path = p.default_vault_path()
        p.save(update_fields=["vault_path"])
        fake_vault[p.vault_path] = {"client_secret": "db-secret"}
        assert _FakeBackend().get_key_and_secret() == ("db-key", "db-secret")

    def test_falls_back_to_static_when_no_provider(self, fake_vault):
        assert _FakeBackend().get_key_and_secret() == ("static-key", "static-secret")


# ── pipeline ──────────────────────────────────────────────────────────────────

class _Backend:
    name = "google-oauth2"


class TestPipeline:
    def test_check_allowed_domain_blocks_foreign(self):
        SSOProvider.objects.create(name="g", provider="google-oauth2",
                                   allowed_domains=["company.com"])
        with pytest.raises(AuthForbidden):
            pipeline.check_allowed_domain(_Backend(), {"email": "x@evil.com"}, user=None)

    def test_check_allowed_domain_allows_match(self):
        SSOProvider.objects.create(name="g", provider="google-oauth2",
                                   allowed_domains=["company.com"])
        # No exception → allowed.
        pipeline.check_allowed_domain(_Backend(), {"email": "x@company.com"}, user=None)

    def test_signup_gate_blocks_new_user(self):
        SSOProvider.objects.create(name="g", provider="google-oauth2", allow_signup=False)
        with pytest.raises(AuthForbidden):
            pipeline.check_allowed_domain(_Backend(), {"email": "x@company.com"}, user=None)

    def test_assign_default_role_new_user(self, django_user_model):
        SSOProvider.objects.create(name="g", provider="google-oauth2", default_role="engineer")
        u = django_user_model.objects.create(username="newbie", role="viewer")
        pipeline.assign_default_role(_Backend(), u, is_new=True)
        u.refresh_from_db()
        assert u.role == "engineer"

    def test_assign_default_role_skips_existing(self, django_user_model):
        SSOProvider.objects.create(name="g", provider="google-oauth2", default_role="engineer")
        u = django_user_model.objects.create(username="old", role="admin")
        pipeline.assign_default_role(_Backend(), u, is_new=False)
        u.refresh_from_db()
        assert u.role == "admin"                          # untouched

    def test_sync_user_profile_fills_name_email(self, django_user_model):
        u = django_user_model.objects.create(username="jdoe")
        pipeline.sync_user_profile(
            _Backend(), u, {"fullname": "John Doe", "email": "john@company.com"})
        u.refresh_from_db()
        assert u.first_name == "John" and u.last_name == "Doe"
        assert u.email == "john@company.com"


# ── JWT bridge ──────────────────────────────────────────────────────────────────

class TestJwtBridge:
    def test_get_tokens_for_user(self, admin_user):
        tokens = get_tokens_for_user(admin_user)
        assert set(tokens) == {"access", "refresh"}
        assert tokens["access"] and tokens["refresh"]
