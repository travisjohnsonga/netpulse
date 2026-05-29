from django.urls import path

from .chatops import webhook_discord, webhook_gchat, webhook_slack, webhook_teams
from .views import health, infrastructure_health

urlpatterns = [
    path("health/", health, name="health"),
    path("health/infrastructure/", infrastructure_health, name="health-infrastructure"),
    # ChatOps webhook receivers
    path("webhooks/slack/",   webhook_slack,   name="webhook-slack"),
    path("webhooks/teams/",   webhook_teams,   name="webhook-teams"),
    path("webhooks/gchat/",   webhook_gchat,   name="webhook-gchat"),
    path("webhooks/discord/", webhook_discord, name="webhook-discord"),
]
