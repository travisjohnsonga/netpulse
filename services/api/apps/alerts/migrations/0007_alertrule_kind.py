# Two-tier rule classification (rule-management arc): add AlertRule.kind and
# backfill existing rules so both fresh AND already-seeded (seed-once) installs
# get correct kinds. System-tier = spane's own machinery; everything else is
# operational (the customer's network/servers).

from django.db import migrations, models

# Kept in sync with apps.alerts.models.SYSTEM_TIER_RULE_NAMES. Inlined here so
# the migration is self-contained (migrations must not import runtime constants
# that may change shape over time).
SYSTEM_TIER_RULE_NAMES = ["Notification Delivery Failed"]


def backfill_kind(apps, schema_editor):
    """Existing installs are already seeded+marked (seed-once won't re-run), so
    classify their rules here: known system-tier rules → 'system', the rest stay
    'operational' (the AddField default already set them)."""
    AlertRule = apps.get_model("alerts", "AlertRule")
    AlertRule.objects.filter(name__in=SYSTEM_TIER_RULE_NAMES).update(kind="system")


def noop_reverse(apps, schema_editor):
    # Reversing just drops the column (handled by the AddField reverse); nothing
    # to undo on the data side.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("alerts", "0006_alertrule_notify_enabled"),
    ]

    operations = [
        migrations.AddField(
            model_name="alertrule",
            name="kind",
            field=models.CharField(
                choices=[("system", "System"), ("operational", "Operational")],
                db_index=True,
                default="operational",
                max_length=16,
            ),
        ),
        migrations.RunPython(backfill_kind, noop_reverse),
    ]
