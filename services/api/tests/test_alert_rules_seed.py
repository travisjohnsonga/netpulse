"""Tests for default (system) alert rules: seeding, protection, suppression."""
import pytest
from django.core.management import call_command

from apps.alerts.models import AlertRule

pytestmark = pytest.mark.django_db

SYSTEM_RULE_NAMES = {
    "Interface State Change",
    "device-unreachable",
    "service-check-failed",
    "flow-threshold-exceeded",
    "latency-threshold-exceeded",
    "log-anomaly-detected",
    "High Temperature Warning",
    "High Temperature Critical",
    "Temperature Sensor Failed",
}


class TestSeedAlertRules:
    def test_seed_creates_system_rules(self):
        call_command("seed_alert_rules")
        seeded = set(AlertRule.objects.filter(is_system=True).values_list("name", flat=True))
        assert SYSTEM_RULE_NAMES <= seeded

    def test_temperature_rules_seeded_with_severities(self):
        call_command("seed_alert_rules")
        rules = {r.name: r for r in AlertRule.objects.filter(is_system=True)}
        assert rules["High Temperature Warning"].severity == "medium"
        assert rules["High Temperature Critical"].severity == "critical"
        assert rules["Temperature Sensor Failed"].severity == "high"

    def test_seed_is_idempotent(self):
        call_command("seed_alert_rules")
        count = AlertRule.objects.count()
        call_command("seed_alert_rules")
        assert AlertRule.objects.count() == count

    def test_seed_preserves_user_toggle(self):
        """Re-seeding must not re-enable a rule the operator disabled."""
        call_command("seed_alert_rules")
        rule = AlertRule.objects.get(name="device-unreachable")
        rule.is_active = False
        rule.severity = "low"
        rule.save(update_fields=["is_active", "severity"])

        call_command("seed_alert_rules")
        rule.refresh_from_db()
        assert rule.is_active is False
        assert rule.severity == "low"

    def test_seed_adopts_existing_engine_rule_as_system(self):
        """A rule auto-created by an engine becomes a protected system rule."""
        AlertRule.objects.create(
            name="flow-threshold-exceeded", severity="high",
            condition={"source": "stream-processor"}, is_system=False,
        )
        call_command("seed_alert_rules")
        rule = AlertRule.objects.get(name="flow-threshold-exceeded")
        assert rule.is_system is True


class TestSystemRuleProtection:
    def test_system_rule_cannot_be_deleted(self, auth_client):
        call_command("seed_alert_rules")
        rule = AlertRule.objects.get(name="device-unreachable")
        resp = auth_client.delete(f"/api/alerts/rules/{rule.pk}/")
        assert resp.status_code == 403
        assert AlertRule.objects.filter(pk=rule.pk).exists()

    def test_non_system_rule_can_be_deleted(self, auth_client):
        rule = AlertRule.objects.create(
            name="Custom rule", severity="low", condition={}, is_system=False,
        )
        resp = auth_client.delete(f"/api/alerts/rules/{rule.pk}/")
        assert resp.status_code == 204
        assert not AlertRule.objects.filter(pk=rule.pk).exists()

    def test_is_system_is_read_only_via_api(self, auth_client):
        rule = AlertRule.objects.create(name="Custom", severity="low", condition={})
        resp = auth_client.patch(
            f"/api/alerts/rules/{rule.pk}/", {"is_system": True}, format="json")
        assert resp.status_code == 200
        rule.refresh_from_db()
        assert rule.is_system is False

    def test_system_rule_can_still_be_disabled(self, auth_client):
        call_command("seed_alert_rules")
        rule = AlertRule.objects.get(name="device-unreachable")
        resp = auth_client.patch(
            f"/api/alerts/rules/{rule.pk}/", {"is_active": False}, format="json")
        assert resp.status_code == 200
        rule.refresh_from_db()
        assert rule.is_active is False


class TestDisabledRuleSuppressesAlerts:
    def test_disabled_rule_suppresses_stream_processor_event(self):
        """_db_write_alert must skip event creation when the rule is disabled."""
        from apps.telemetry.management.commands.run_stream_processor import Command

        AlertRule.objects.create(
            name="flow-threshold-exceeded", severity="high",
            condition={}, is_active=False,
        )
        from apps.alerts.models import AlertEvent
        Command._db_write_alert("high", {
            "rule_name": "flow-threshold-exceeded",
            "labels": {"exporter_ip": "10.0.0.1"},
        })
        assert AlertEvent.objects.filter(rule__name="flow-threshold-exceeded").count() == 0

    def test_active_rule_creates_stream_processor_event(self):
        from apps.telemetry.management.commands.run_stream_processor import Command
        from apps.alerts.models import AlertEvent

        Command._db_write_alert("high", {
            "rule_name": "flow-threshold-exceeded",
            "labels": {"exporter_ip": "10.0.0.1"},
        })
        assert AlertEvent.objects.filter(rule__name="flow-threshold-exceeded").count() == 1
