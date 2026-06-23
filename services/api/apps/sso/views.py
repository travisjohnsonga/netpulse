import logging

from django.conf import settings
from django.shortcuts import redirect
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from apps.core.errors import log_internal_error
from apps.core.permissions import HasCapability
from apps.credentials import vault
from .models import SSOProvider
from .serializers import SSOProviderAdminSerializer, SSOProviderPublicSerializer

logger = logging.getLogger(__name__)


def get_tokens_for_user(user) -> dict:
    """Mint the same JWT pair as local login — including the custom username/
    role/name/email claims (RefreshToken.for_user alone omits them, which left
    SSO users without a username/role in the token)."""
    from apps.core.serializers import NetPulseTokenObtainPairSerializer
    refresh = NetPulseTokenObtainPairSerializer.get_token(user)
    return {"access": str(refresh.access_token), "refresh": str(refresh)}


class SSOProviderViewSet(viewsets.ModelViewSet):
    """
    list  → public (no auth): enabled providers for the login page buttons.
    other → sso:manage: full CRUD with secret handling via OpenBao.
    """

    queryset = SSOProvider.objects.all()

    def _request_is_admin(self) -> bool:
        u = getattr(self.request, "user", None)
        return bool(u and u.is_authenticated and (u.is_superuser or getattr(u, "role", "") == "admin"))

    def get_permissions(self):
        # list is public (login page buttons); everything else (incl. test)
        # requires sso:manage.
        return [AllowAny()] if self.action == "list" else [HasCapability("sso:manage")()]

    def get_serializer_class(self):
        # Anonymous / non-admin callers see the public shape; admins get full
        # config so the Settings management page can render every field.
        if self.action == "list" and not self._request_is_admin():
            return SSOProviderPublicSerializer
        return SSOProviderAdminSerializer

    def get_queryset(self):
        # Public list is enabled-only; admins see all (incl. disabled) to manage.
        if self.action == "list" and not self._request_is_admin():
            return SSOProvider.objects.filter(is_enabled=True)
        return SSOProvider.objects.all()

    @action(detail=True, methods=["post"])
    def test(self, request, pk=None):
        """Validate a provider's configuration. Returns {valid, error}."""
        provider = self.get_object()
        errors = []
        if not provider.client_id:
            errors.append("client_id is not set")
        secret = ""
        if provider.vault_path:
            try:
                secret = (vault.read_secret(provider.vault_path) or {}).get("client_secret", "")
            except Exception as exc:  # noqa: BLE001
                log_internal_error(exc, logger, "sso validate: OpenBao read")
                errors.append("OpenBao read failed")
        if not secret:
            errors.append("client_secret is not stored in OpenBao")
        if provider.provider == SSOProvider.Provider.AZURE and not provider.tenant_id:
            errors.append("tenant_id is required for Azure AD")
        if provider.provider == SSOProvider.Provider.OKTA and not provider.okta_domain:
            errors.append("okta_domain is required for Okta")
        return Response({"valid": not errors, "error": "; ".join(errors) or None})


def sso_jwt_redirect(request):
    """
    Final hop after social-auth completes. The user is authenticated in the
    Django session at this point; mint NetPulse JWTs and hand them to the SPA via
    the URL fragment (the frontend reads then clears it). On failure, bounce to
    the login page with an error flag.
    """
    frontend = (getattr(settings, "FRONTEND_BASE_URL", "") or "").rstrip("/")
    if not request.user.is_authenticated:
        return redirect(f"{frontend}/login?sso_error=auth_failed")
    tokens = get_tokens_for_user(request.user)
    return redirect(f"{frontend}/#token={tokens['access']}&refresh={tokens['refresh']}")
