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


class TestRuleKinds:
    """Two-tier classification: system (spane machinery) vs operational (customer)."""

    def test_fresh_seed_rules_are_all_operational(self):
        call_command("seed_alert_rules")
        kinds = set(AlertRule.objects.values_list("kind", flat=True))
        # Every seeded default monitors the customer's network/servers.
        assert kinds == {"operational"}
        assert not AlertRule.objects.filter(kind="system").exists()

    def test_specific_operational_rules_classified(self):
        call_command("seed_alert_rules")
        for name in ("device-unreachable", "Config Changed", "High Temperature Warning",
                     "High PoE Usage", "High WAN Utilization"):
            assert AlertRule.objects.get(name=name).kind == "operational"

    def test_new_rule_defaults_to_operational(self):
        rule = AlertRule.objects.create(name="Custom", severity="low", condition={})
        assert rule.kind == "operational"

    def test_backfill_reclassifies_meta_alarm_as_system(self):
        """The data migration's backfill logic: known system-tier rules → 'system',
        everything else stays operational (upgrade path for existing installs)."""
        import importlib

        from django.apps import apps as global_apps

        call_command("seed_alert_rules")  # operational defaults
        # Simulate a pre-kinds meta-alarm row (created before kind existed → default).
        meta = AlertRule.objects.create(
            name="Notification Delivery Failed", severity="high", condition={"meta": True})
        assert meta.kind == "operational"

        mod = importlib.import_module("apps.alerts.migrations.0007_alertrule_kind")
        mod.backfill_kind(global_apps, None)

        meta.refresh_from_db()
        assert meta.kind == "system"                       # reclassified
        # Operational rules are untouched by the backfill.
        assert AlertRule.objects.get(name="device-unreachable").kind == "operational"
        assert AlertRule.objects.filter(kind="system").count() == 1

    def test_kind_is_read_only_via_api(self, auth_client):
        rule = AlertRule.objects.create(name="Custom", severity="low", condition={})
        resp = auth_client.patch(
            f"/api/alerts/rules/{rule.pk}/", {"kind": "system"}, format="json")
        assert resp.status_code == 200
        rule.refresh_from_db()
        assert rule.kind == "operational"


class TestKindAwareProtection:
    """Delete protection is KIND-aware: only Tier-1 system rules are blocked."""

    def test_system_kind_rule_cannot_be_deleted(self, auth_client):
        rule = AlertRule.objects.create(
            name="Notification Delivery Failed", severity="high", condition={"meta": True},
            kind=AlertRule.Kind.SYSTEM,
        )
        resp = auth_client.delete(f"/api/alerts/rules/{rule.pk}/")
        assert resp.status_code == 403
        assert AlertRule.objects.filter(pk=rule.pk).exists()

    def test_operational_seeded_rule_is_now_deletable(self, auth_client):
        """The transition: a seeded built-in (is_system=True) that is Tier-2
        OPERATIONAL is now deletable — protection follows kind, not is_system."""
        call_command("seed_alert_rules")
        rule = AlertRule.objects.get(name="device-unreachable")
        assert rule.is_system is True and rule.kind == "operational"
        resp = auth_client.delete(f"/api/alerts/rules/{rule.pk}/")
        assert resp.status_code == 204
        assert not AlertRule.objects.filter(pk=rule.pk).exists()

    def test_deleted_operational_rule_stays_deleted_after_seed(self, auth_client):
        """Seed-once proof: a deleted rule does not resurrect on the next seed run."""
        call_command("seed_alert_rules")
        rule = AlertRule.objects.get(name="High PoE Usage")
        auth_client.delete(f"/api/alerts/rules/{rule.pk}/")
        call_command("seed_alert_rules")  # reboot
        assert not AlertRule.objects.filter(name="High PoE Usage").exists()

    def test_custom_rule_can_be_deleted(self, auth_client):
        rule = AlertRule.objects.create(name="Custom rule", severity="low", condition={})
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

    def test_system_rule_disable_is_allowed_by_backend(self, auth_client):
        """The disable warning is UI-side; the backend must ALLOW a system-rule
        disable (is_active=False) — it never blocks the toggle."""
        rule = AlertRule.objects.create(
            name="Notification Delivery Failed", severity="high", condition={"meta": True},
            kind=AlertRule.Kind.SYSTEM,
        )
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
