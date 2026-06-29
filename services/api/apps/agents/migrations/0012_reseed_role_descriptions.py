"""Re-seed built-in role profiles so existing rows pick up the improved intent
descriptions (the seed only ran once in 0002; update_or_create refreshes them).
Idempotent + safe to re-run."""
from django.db import migrations


def reseed(apps, schema_editor):
    from apps.agents.seed import seed_builtin_roles
    seed_builtin_roles(apps.get_model("agents", "ServerRole"))


class Migration(migrations.Migration):

    dependencies = [
        ("agents", "0011_agent_last_ip"),
    ]

    operations = [
        migrations.RunPython(reseed, migrations.RunPython.noop),
    ]
