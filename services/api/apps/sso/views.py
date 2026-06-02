from django.conf import settings
from django.shortcuts import redirect
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework_simplejwt.tokens import RefreshToken

from apps.core.permissions import AdminOnly
from apps.credentials import vault
from .models import SSOProvider
from .serializers import SSOProviderAdminSerializer, SSOProviderPublicSerializer


def get_tokens_for_user(user) -> dict:
    """Mint the same JWT pair as local login (DRF SimpleJWT)."""
    refresh = RefreshToken.for_user(user)
    return {"access": str(refresh.access_token), "refresh": str(refresh)}


class SSOProviderViewSet(viewsets.ModelViewSet):
    """
    list  → public (no auth): enabled providers for the login page buttons.
    other → AdminOnly: full CRUD with secret handling via OpenBao.
    """

    queryset = SSOProvider.objects.all()

    def get_permissions(self):
        return [AllowAny()] if self.action == "list" else [AdminOnly()]

    def get_serializer_class(self):
        return SSOProviderPublicSerializer if self.action == "list" else SSOProviderAdminSerializer

    def get_queryset(self):
        if self.action == "list":
            return SSOProvider.objects.filter(is_enabled=True)
        return SSOProvider.objects.all()

    @action(detail=True, methods=["post"], permission_classes=[AdminOnly])
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
                errors.append(f"OpenBao read failed: {exc}")
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
