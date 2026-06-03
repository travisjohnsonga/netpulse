"""
SSO auth backends with dynamic (DB + OpenBao) credential resolution.

social-auth normally reads a backend's settings from static Django settings
(SOCIAL_AUTH_<BACKEND>_KEY/SECRET/TENANT_ID/API_URL...). NetPulse stores them
per-provider in the database (client_id, tenant_id, okta_domain) and OpenBao
(client_secret), so each backend mixes in `_DBCredentialsMixin`, which:

  - overrides ``get_key_and_secret()`` — social-core's seam for OAuth creds —
    to return the active SSOProvider's client_id + the OpenBao client_secret;
  - overrides ``setting()`` so provider-specific extras (Azure TENANT_ID, Okta
    API_URL) resolve from the DB row at request time.

Both fall back to the static SOCIAL_AUTH_* setting when no enabled provider row
exists, so env-configured static credentials still work.
"""
from __future__ import annotations

import logging

from social_core.backends.azuread_tenant import AzureADV2TenantOAuth2
from social_core.backends.github import GithubOAuth2
from social_core.backends.google import GoogleOAuth2
from social_core.backends.okta import OktaOAuth2

logger = logging.getLogger(__name__)


class _DBCredentialsMixin:
    """Resolve credentials/settings from the matching enabled SSOProvider + OpenBao."""

    # Subclasses keep social-core's ``name`` (e.g. "google-oauth2"); we match
    # SSOProvider.provider against it.

    def _provider(self):
        # Cache for the lifetime of this (per-request) backend instance so a
        # single auth flow doesn't re-query for every setting() lookup.
        if not hasattr(self, "_cached_provider"):
            from apps.sso.models import SSOProvider
            self._cached_provider = SSOProvider.objects.filter(
                provider=self.name, is_enabled=True).first()
        return self._cached_provider

    def _db_secret(self, provider) -> str:
        if not provider or not provider.vault_path:
            return ""
        try:
            from apps.credentials import vault
            return (vault.read_secret(provider.vault_path) or {}).get("client_secret", "")
        except Exception as exc:  # noqa: BLE001 — fall back to static settings
            logger.warning("SSO: could not read client_secret from OpenBao: %s", exc)
            return ""

    def _db_extra(self, name: str, provider):
        """
        Provider-specific extra settings resolved from the DB row.
        Subclasses override; return a truthy value to use it, else None to fall
        back to the static SOCIAL_AUTH_* setting.
        """
        return None

    def get_key_and_secret(self):
        provider = self._provider()
        if provider:
            static_key, static_secret = super().get_key_and_secret()
            return (provider.client_id or static_key, self._db_secret(provider) or static_secret)
        return super().get_key_and_secret()

    def setting(self, name, default=None):
        provider = self._provider()
        if provider is not None:
            # Route KEY/SECRET to the DB+OpenBao too (not just via
            # get_key_and_secret) — social-core reads self.setting("KEY")
            # directly when validating the id_token audience, so a UI-only
            # provider (no static env var) must still resolve it here.
            if name == "KEY" and provider.client_id:
                return provider.client_id
            if name == "SECRET":
                secret = self._db_secret(provider)
                if secret:
                    return secret
            val = self._db_extra(name, provider)
            if val:
                return val
        return super().setting(name, default)


class DynamicGoogleOAuth2(_DBCredentialsMixin, GoogleOAuth2):
    """Google OAuth2 — client_id/secret from DB + OpenBao. name='google-oauth2'."""


class DynamicAzureADTenantOAuth2(_DBCredentialsMixin, AzureADV2TenantOAuth2):
    """
    Azure AD (tenant) OAuth2 on the v2.0 endpoints (oauth2/v2.0/authorize +
    token). Keeps name='azuread-tenant-oauth2' so existing providers, the
    /auth/login/azuread-tenant-oauth2/ URL, and the env seed are unchanged.

    Why v2: v1 tokens (/oauth2/token) set aud = the resource URI, which fails
    social-core's id_token audience check (audience = client_id). v2 id_tokens
    set aud = the client_id, so validation passes. Adds TENANT_ID from the DB.
    """
    name = "azuread-tenant-oauth2"

    def _db_extra(self, name, provider):
        if name == "TENANT_ID" and provider.tenant_id:
            return provider.tenant_id
        return None


class DynamicOktaOAuth2(_DBCredentialsMixin, OktaOAuth2):
    """Okta OAuth2 — derives API_URL from okta_domain. name='okta-oauth2'."""

    def _db_extra(self, name, provider):
        if name == "API_URL" and provider.okta_domain:
            return f"https://{provider.okta_domain}/oauth2/default"
        return None


class DynamicGithubOAuth2(_DBCredentialsMixin, GithubOAuth2):
    """GitHub OAuth2 — client_id/secret from DB + OpenBao. name='github'."""
