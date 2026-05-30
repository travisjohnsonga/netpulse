from django.urls import path

from .chatops import webhook_discord, webhook_gchat, webhook_slack, webhook_teams
from .views import (
    ChangePasswordView,
    MeView,
    MyPreferencesView,
    health,
    infrastructure_health,
)

urlpatterns = [
    path("health/", health, name="health"),
    path("health/infrastructure/", infrastructure_health, name="health-infrastructure"),
    # Current user profile & preferences
    path("users/me/",                 MeView.as_view(),             name="users-me"),
    path("users/me/preferences/",     MyPreferencesView.as_view(),  name="users-me-preferences"),
    path("users/me/change-password/", ChangePasswordView.as_view(), name="users-me-change-password"),
    # ChatOps webhook receivers
    path("webhooks/slack/",   webhook_slack,   name="webhook-slack"),
    path("webhooks/teams/",   webhook_teams,   name="webhook-teams"),
    path("webhooks/gchat/",   webhook_gchat,   name="webhook-gchat"),
    path("webhooks/discord/", webhook_discord, name="webhook-discord"),
]
