"""Compound lldp_capability matching for interface compliance rules.

Adds trigger_require_capabilities (AND) + trigger_exclude_capabilities (NOT) and
backfills the seeded "Switch Uplink Port Config" rule to require "router" — so it
matches switch-to-switch uplinks (which advertise bridge AND router) and no
longer over-matches AP ports (which advertise bridge + wlan-ap).
"""
from django.db import migrations, models


def require_router_on_uplink(apps, schema_editor):
    Rule = apps.get_model("compliance", "InterfaceComplianceRule")
    for rule in Rule.objects.filter(name="Switch Uplink Port Config",
                                    trigger="lldp_capability", trigger_value="bridge"):
        if not (rule.trigger_require_capabilities or []):
            rule.trigger_require_capabilities = ["router"]
            rule.save(update_fields=["trigger_require_capabilities"])


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("compliance", "0006_interfacecompliancerule_roleconsistencyrule_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="interfacecompliancerule",
            name="trigger_require_capabilities",
            field=models.JSONField(
                blank=True, default=list,
                help_text="Neighbour must ALSO advertise ALL of these capabilities (AND)."),
        ),
        migrations.AddField(
            model_name="interfacecompliancerule",
            name="trigger_exclude_capabilities",
            field=models.JSONField(
                blank=True, default=list,
                help_text="Skip the interface if the neighbour advertises ANY of these (NOT)."),
        ),
        migrations.RunPython(require_router_on_uplink, noop),
    ]
