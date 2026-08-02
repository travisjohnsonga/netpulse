from django.urls import path
from rest_framework.routers import SimpleRouter

from .chatops import (
    webhook_discord,
    webhook_gchat,
    webhook_mattermost,
    webhook_slack,
    webhook_teams,
)
from .mfa_views import (
    MFAConfirmView,
    MFADisableView,
    MFASetupView,
    MFAStatusView,
    MFATokenView,
)
from .rbac_views import CapabilityCatalogView, RoleViewSet
from .version import version, version_check
from .views import (
    AuditLogViewSet,
    ChangePasswordView,
    MeView,
    MyPreferencesView,
    OnboardingCompleteView,
    OnboardingStatusView,
    UserViewSet,
    health,
    infrastructure_health,
    setup_status,
)

router = SimpleRouter()
router.register("users", UserViewSet)
router.register("audit-log", AuditLogViewSet, basename="audit-log")
# RBAC Track 2 Phase C: role-management API (all gated by rbac:manage).
router.register("rbac/roles", RoleViewSet, basename="rbac-role")

urlpatterns = [
    path("health/", health, name="health"),
    path("rbac/capabilities/", CapabilityCatalogView.as_view(), name="rbac-capabilities"),
    path("health/infrastructure/", infrastructure_health, name="health-infrastructure"),
    path("setup/status/", setup_status, name="setup-status"),
    path("version/", version, name="version"),
    path("version/check/", version_check, name="version-check"),
    # Current user profile & preferences. These MUST precede the router so
    # /users/me/ resolves here and not to the UserViewSet detail route (pk="me").
    path("users/me/",                 MeView.as_view(),             name="users-me"),
    path("users/me/preferences/",     MyPreferencesView.as_view(),  name="users-me-preferences"),
    path("users/me/change-password/", ChangePasswordView.as_view(), name="users-me-change-password"),
    # Multi-factor auth (TOTP) — enrollment, second factor, status.
    path("auth/mfa/setup/",   MFASetupView.as_view(),   name="mfa-setup"),
    path("auth/mfa/confirm/", MFAConfirmView.as_view(), name="mfa-confirm"),
    path("auth/mfa/disable/", MFADisableView.as_view(), name="mfa-disable"),
    path("auth/mfa/",         MFAStatusView.as_view(),  name="mfa-status"),
    path("auth/token/mfa/",   MFATokenView.as_view(),   name="token-mfa"),
    # Onboarding (Get Started wizard) gating.
    path("onboarding/status/",   OnboardingStatusView.as_view(),   name="onboarding-status"),
    path("onboarding/complete/", OnboardingCompleteView.as_view(), name="onboarding-complete"),
    # ChatOps webhook receivers
    path("webhooks/slack/",   webhook_slack,   name="webhook-slack"),
    path("webhooks/teams/",   webhook_teams,   name="webhook-teams"),
    path("webhooks/gchat/",   webhook_gchat,   name="webhook-gchat"),
    path("webhooks/discord/", webhook_discord, name="webhook-discord"),
    path("webhooks/mattermost/", webhook_mattermost, name="webhook-mattermost"),
    # Admin user management (/users/, /users/{id}/) — AdminOnly.
    *router.urls,
]
