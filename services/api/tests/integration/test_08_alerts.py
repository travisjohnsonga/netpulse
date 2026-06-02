"""Integration: alerts — default system rules seeded + protected; teams/policies."""
import pytest
from django.core.management import call_command

from apps.alerts.management.commands.seed_alert_rules import DEFAULT_RULES
from apps.alerts.models import AlertRule

pytestmark = pytest.mark.django_db


class TestDefaultRules:
    def test_seed_creates_all_default_rules_as_system(self):
        call_command("seed_alert_rules")
        system = AlertRule.objects.filter(is_system=True)
        # Exactly the DEFAULT_RULES set is seeded as system rules.
        assert system.count() == len(DEFAULT_RULES)
        seeded_names = set(system.values_list("name", flat=True))
        assert seeded_names == {name for name, *_ in DEFAULT_RULES}

    def test_system_rule_not_deletable_via_api(self, auth_client):
        call_command("seed_alert_rules")
        rule = AlertRule.objects.filter(is_system=True).first()
        resp = auth_client.delete(f"/api/alerts/rules/{rule.pk}/")
        assert resp.status_code == 403
        assert AlertRule.objects.filter(pk=rule.pk).exists()

    def test_custom_rule_deletable(self, auth_client):
        rule = AlertRule.objects.create(name="Custom", severity="low",
                                        condition={}, is_system=False)
        resp = auth_client.delete(f"/api/alerts/rules/{rule.pk}/")
        assert resp.status_code == 204


class TestTeamsAndPolicies:
    def test_create_team(self, auth_client):
        resp = auth_client.post(
            "/api/alerting/teams/",
            {"name": "NOC Tier 1", "color": "#3b82f6"}, format="json",
        )
        assert resp.status_code == 201, resp.content
        assert resp.json()["name"] == "NOC Tier 1"

    def test_create_escalation_policy(self, auth_client):
        team = auth_client.post(
            "/api/alerting/teams/", {"name": "Escalation Team"}, format="json"
        ).json()
        resp = auth_client.post(
            "/api/alerting/policies/",
            {"name": "Critical Path", "team": team["id"],
             "repeat_interval_minutes": 15},
            format="json",
        )
        assert resp.status_code == 201, resp.content
        body = resp.json()
        assert body["team"] == team["id"]
        assert body["repeat_interval_minutes"] == 15

    def test_unauthenticated_rejected(self, api_client):
        assert api_client.get("/api/alerting/teams/").status_code == 401
