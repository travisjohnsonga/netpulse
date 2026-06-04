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

    def test_sso_token_carries_custom_claims(self, django_user_model):
        # The SSO JWT must include the same username/role/name/email claims as
        # local login, or the sidebar (which reads them) breaks for SSO users.
        import json
        from base64 import urlsafe_b64decode
        user = django_user_model.objects.create_user(
            username="ssouser", email="sso@company.com", password="x",
            first_name="Sso", last_name="User", role="viewer",
        )
        access = get_tokens_for_user(user)["access"]
        payload_b64 = access.split(".")[1]
        payload_b64 += "=" * (-len(payload_b64) % 4)  # pad for base64
        claims = json.loads(urlsafe_b64decode(payload_b64))
        assert claims["username"] == "ssouser"
        assert claims["role"] == "viewer"
        assert claims["name"] == "Sso User"
        assert claims["email"] == "sso@company.com"


# ── Stage 2: Azure AD + Okta + GitHub backends ──────────────────────────────────

class _FakeStrategy:
    """Minimal social-core strategy: returns the default for every setting."""

    def setting(self, name, default=None, backend=None):
        return default


def _mk(cls):
    """
    Build a backend without social-core's __init__ (which needs a full request
    strategy). The mixin only uses self.name + self.strategy, so this is enough
    to exercise get_key_and_secret()/setting().
    """
    b = cls.__new__(cls)
    b.strategy = _FakeStrategy()
    b.redirect_uri = None
    b.data = {}
    return b


def _named_backend(name):
    class _B:
        pass
    b = _B()
    b.name = name
    return b


class TestStage2Backends:
    def test_azure_ad_backend_reads_tenant_id(self, fake_vault):
        from apps.sso.backends import DynamicAzureADTenantOAuth2
        p = SSOProvider.objects.create(
            name="Azure", provider="azuread-tenant-oauth2", client_id="az-key",
            tenant_id="tid-123", is_enabled=True)
        p.vault_path = p.default_vault_path()
        p.save(update_fields=["vault_path"])
        fake_vault[p.vault_path] = {"client_secret": "az-secret"}

        b = _mk(DynamicAzureADTenantOAuth2)
        assert b.setting("TENANT_ID") == "tid-123"
        assert b.get_key_and_secret() == ("az-key", "az-secret")

    def test_azure_ad_domain_restriction(self):
        SSOProvider.objects.create(
            name="Azure", provider="azuread-tenant-oauth2",
            allowed_domains=["company.com"], is_enabled=True)
        backend = _named_backend("azuread-tenant-oauth2")
        with pytest.raises(AuthForbidden):
            pipeline.check_allowed_domain(backend, {"email": "x@other.com"}, user=None)
        # Allowed domain → no exception.
        pipeline.check_allowed_domain(backend, {"email": "x@company.com"}, user=None)

    def test_okta_backend_builds_api_url(self):
        from apps.sso.backends import DynamicOktaOAuth2
        SSOProvider.objects.create(
            name="Okta", provider="okta-oauth2", client_id="ok-key",
            okta_domain="company.okta.com", is_enabled=True)
        b = _mk(DynamicOktaOAuth2)
        assert b.setting("API_URL") == "https://company.okta.com/oauth2/default"

    def test_github_backend_config(self, fake_vault):
        from apps.sso.backends import DynamicGithubOAuth2
        p = SSOProvider.objects.create(
            name="GitHub", provider="github", client_id="gh-key", is_enabled=True)
        p.vault_path = p.default_vault_path()
        p.save(update_fields=["vault_path"])
        fake_vault[p.vault_path] = {"client_secret": "gh-secret"}
        b = _mk(DynamicGithubOAuth2)
        assert b.name == "github"
        assert b.get_key_and_secret() == ("gh-key", "gh-secret")

    def test_provider_choices_include_all_providers(self):
        values = set(SSOProvider.Provider.values)
        assert {"google-oauth2", "azuread-tenant-oauth2", "okta-oauth2",
                "github", "saml", "ldap"} <= values

    def test_authentication_backends_registered(self, settings):
        for b in ("DynamicGoogleOAuth2", "DynamicAzureADTenantOAuth2",
                  "DynamicOktaOAuth2", "DynamicGithubOAuth2"):
            assert f"apps.sso.backends.{b}" in settings.AUTHENTICATION_BACKENDS
        assert "django.contrib.auth.backends.ModelBackend" in settings.AUTHENTICATION_BACKENDS


class TestSeedSSOProviders:
    def test_seed_sso_providers_from_env(self, monkeypatch, fake_vault):
        from django.core.management import call_command
        for prov in ("AZUREAD_TENANT_OAUTH2", "OKTA_OAUTH2", "GOOGLE_OAUTH2", "GITHUB"):
            monkeypatch.delenv(f"SOCIAL_AUTH_{prov}_KEY", raising=False)
        monkeypatch.setenv("SOCIAL_AUTH_GITHUB_KEY", "gh-client")
        monkeypatch.setenv("SOCIAL_AUTH_GITHUB_SECRET", "gh-secret")

        call_command("seed_sso_providers")
        gh = SSOProvider.objects.get(provider="github")
        assert gh.client_id == "gh-client" and gh.is_enabled
        assert fake_vault[gh.vault_path]["client_secret"] == "gh-secret"

        # Idempotent — a second run doesn't duplicate.
        call_command("seed_sso_providers")
        assert SSOProvider.objects.filter(provider="github").count() == 1

    def test_seed_azure_from_env_with_tenant(self, monkeypatch, fake_vault):
        from django.core.management import call_command
        for prov in ("OKTA_OAUTH2", "GOOGLE_OAUTH2", "GITHUB"):
            monkeypatch.delenv(f"SOCIAL_AUTH_{prov}_KEY", raising=False)
        monkeypatch.setenv("SOCIAL_AUTH_AZUREAD_TENANT_OAUTH2_KEY", "az-client")
        monkeypatch.setenv("SOCIAL_AUTH_AZUREAD_TENANT_OAUTH2_TENANT_ID", "tid-9")
        monkeypatch.setenv("SOCIAL_AUTH_AZUREAD_TENANT_OAUTH2_SECRET", "az-secret")

        call_command("seed_sso_providers")
        az = SSOProvider.objects.get(provider="azuread-tenant-oauth2")
        assert az.client_id == "az-client" and az.tenant_id == "tid-9" and az.is_enabled


class TestSSOAdminList:
    def test_admin_list_includes_disabled_and_full_fields(self, auth_client):
        SSOProvider.objects.create(name="On", provider="google-oauth2", is_enabled=True,
                                   client_id="cid")
        SSOProvider.objects.create(name="Off", provider="github", is_enabled=False)
        resp = auth_client.get("/api/sso/providers/")
        assert resp.status_code == 200
        data = resp.json()
        rows = data["results"] if isinstance(data, dict) and "results" in data else data
        names = sorted(r["name"] for r in rows)
        assert names == ["Off", "On"]              # disabled included for admins
        # Admin serializer exposes management fields.
        on = next(r for r in rows if r["name"] == "On")
        assert "is_enabled" in on and "allow_signup" in on and "client_id" in on
        assert "client_secret" not in on            # still write-only


class TestAzureV2Endpoints:
    def test_azure_backend_uses_v2_endpoints(self):
        from apps.sso.backends import DynamicAzureADTenantOAuth2 as B
        # Name unchanged (existing provider rows / URLs / seed keep working)…
        assert B.name == "azuread-tenant-oauth2"
        # …but the endpoints are v2.0 (v1 token aud = resource → Invalid audience).
        assert "v2.0" in B.AUTHORIZATION_URL
        assert "v2.0" in B.ACCESS_TOKEN_URL
        assert "v2.0" in B.OPENID_CONFIGURATION_URL

    def test_azure_key_and_secret_resolve_from_db(self, fake_vault):
        from apps.sso.backends import DynamicAzureADTenantOAuth2
        p = SSOProvider.objects.create(
            name="Azure", provider="azuread-tenant-oauth2", client_id="az-client",
            tenant_id="tid-1", is_enabled=True)
        p.vault_path = p.default_vault_path()
        p.save(update_fields=["vault_path"])
        fake_vault[p.vault_path] = {"client_secret": "az-secret"}
        b = _mk(DynamicAzureADTenantOAuth2)
        # social-core reads setting("KEY") directly when validating the id_token
        # audience — it must resolve to the DB client_id, not a static env var.
        assert b.setting("KEY") == "az-client"
        assert b.setting("SECRET") == "az-secret"
        assert b.setting("TENANT_ID") == "tid-1"
