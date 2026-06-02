"""
SSO auth backends with dynamic (DB + OpenBao) credential resolution.

social-auth normally reads a backend's client id/secret from static Django
settings (SOCIAL_AUTH_<BACKEND>_KEY/SECRET). NetPulse stores them per-provider
in the database (client_id) and OpenBao (client_secret), so each backend
overrides ``get_key_and_secret()`` — the single method social-core uses to
resolve OAuth credentials — to read from the active SSOProvider row at request
time. Falls back to the static setting when no enabled provider exists.
"""
from __future__ import annotations

import logging

from social_core.backends.google import GoogleOAuth2

logger = logging.getLogger(__name__)


class _DBCredentialsMixin:
    """Resolve (key, secret) from the matching enabled SSOProvider + OpenBao."""

    # Subclasses keep social-core's ``name`` (e.g. "google-oauth2"); we match
    # SSOProvider.provider against it.

    def _provider(self):
        from apps.sso.models import SSOProvider
        return SSOProvider.objects.filter(provider=self.name, is_enabled=True).first()

    def _db_secret(self, provider) -> str:
        if not provider or not provider.vault_path:
            return ""
        try:
            from apps.credentials import vault
            return (vault.read_secret(provider.vault_path) or {}).get("client_secret", "")
        except Exception as exc:  # noqa: BLE001 — fall back to static settings
            logger.warning("SSO: could not read client_secret from OpenBao: %s", exc)
            return ""

    def get_key_and_secret(self):
        provider = self._provider()
        if provider:
            static_key, static_secret = super().get_key_and_secret()
            return (provider.client_id or static_key, self._db_secret(provider) or static_secret)
        return super().get_key_and_secret()


class DynamicGoogleOAuth2(_DBCredentialsMixin, GoogleOAuth2):
    """Google OAuth2 backend reading client_id/secret from the DB + OpenBao."""
    # ``name`` stays "google-oauth2" (inherited) so /auth/{login,complete}/ URLs
    # and the SOCIAL_AUTH_PIPELINE resolve against the same backend.
