"""Tests for default (system) alert rules: seeding, protection, suppression."""
import pytest
from django.core.management import call_command

from apps.alerts.models import AlertRule
from apps.core.models import SeedMarker

pytestmark = pytest.mark.django_db

SEED_KEY = "alert_rules"

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


class TestSeedOnceBootstrap:
    """The seed runs exactly once; deletions stick across reboots."""

    def test_fresh_install_seeds_and_sets_marker(self):
        assert not SeedMarker.is_seeded(SEED_KEY)
        assert AlertRule.objects.count() == 0

        call_command("seed_alert_rules")

        assert SeedMarker.is_seeded(SEED_KEY)
        assert AlertRule.objects.filter(is_system=True).count() > 0

    def test_rerun_after_seed_skips_entirely(self):
        """A reboot (re-run) must not recreate anything — marker respected."""
        call_command("seed_alert_rules")
        count = AlertRule.objects.count()
        marker_id = SeedMarker.objects.get(seed_key=SEED_KEY).pk

        call_command("seed_alert_rules")

        assert AlertRule.objects.count() == count
        # same marker row, not a duplicate
        assert SeedMarker.objects.filter(seed_key=SEED_KEY).count() == 1
        assert SeedMarker.objects.get(seed_key=SEED_KEY).pk == marker_id

    def test_deleted_rule_stays_deleted_after_reseed(self):
        """The whole point: an operator deletion survives the next seed run."""
        call_command("seed_alert_rules")
        rule = AlertRule.objects.get(name="device-unreachable")
        rule.delete()

        call_command("seed_alert_rules")

        assert not AlertRule.objects.filter(name="device-unreachable").exists()

    def test_upgrade_existing_rules_no_marker_marks_without_reseeding(self):
        """Existing install (rules, no marker) → mark, no re-seed, untouched."""
        # Simulate a pre-seed-once deployment: rules exist, no marker.
        existing = AlertRule.objects.create(
            name="flow-threshold-exceeded", severity="high",
            condition={"source": "stream-processor"}, is_system=False,
        )
        assert not SeedMarker.is_seeded(SEED_KEY)

        call_command("seed_alert_rules")

        # Marker set (past-bootstrap recognized)...
        assert SeedMarker.is_seeded(SEED_KEY)
        # ...but NO defaults were seeded (only the pre-existing rule remains)...
        assert AlertRule.objects.count() == 1
        # ...and the existing rule is untouched (not adopted as system).
        existing.refresh_from_db()
        assert existing.is_system is False

    def test_upgrade_then_reboot_still_skips(self):
        """After the upgrade-mark, subsequent boots keep skipping."""
        AlertRule.objects.create(
            name="flow-threshold-exceeded", severity="high", condition={},
        )
        call_command("seed_alert_rules")   # marks without seeding
        call_command("seed_alert_rules")   # reboot → skip
        assert AlertRule.objects.count() == 1


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
