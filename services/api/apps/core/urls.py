from django.urls import path
from rest_framework.routers import SimpleRouter

from .chatops import webhook_discord, webhook_gchat, webhook_slack, webhook_teams
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

urlpatterns = [
    path("health/", health, name="health"),
    path("health/infrastructure/", infrastructure_health, name="health-infrastructure"),
    path("setup/status/", setup_status, name="setup-status"),
    path("version/", version, name="version"),
    path("version/check/", version_check, name="version-check"),
    # Current user profile & preferences. These MUST precede the router so
    # /users/me/ resolves here and not to the UserViewSet detail route (pk="me").
    path("users/me/",                 MeView.as_view(),             name="users-me"),
    path("users/me/preferences/",     MyPreferencesView.as_view(),  name="users-me-preferences"),
    path("users/me/change-password/", ChangePasswordView.as_view(), name="users-me-change-password"),
    # Onboarding (Get Started wizard) gating.
    path("onboarding/status/",   OnboardingStatusView.as_view(),   name="onboarding-status"),
    path("onboarding/complete/", OnboardingCompleteView.as_view(), name="onboarding-complete"),
    # ChatOps webhook receivers
    path("webhooks/slack/",   webhook_slack,   name="webhook-slack"),
    path("webhooks/teams/",   webhook_teams,   name="webhook-teams"),
    path("webhooks/gchat/",   webhook_gchat,   name="webhook-gchat"),
    path("webhooks/discord/", webhook_discord, name="webhook-discord"),
    # Admin user management (/users/, /users/{id}/) — AdminOnly.
    *router.urls,
]
