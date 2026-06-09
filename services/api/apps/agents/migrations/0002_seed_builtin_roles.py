from django.db import migrations


def forward(apps, schema_editor):
    from apps.agents.seed import seed_builtin_roles
    seed_builtin_roles(apps.get_model("agents", "ServerRole"))


def reverse(apps, schema_editor):
    apps.get_model("agents", "ServerRole").objects.filter(is_builtin=True).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("agents", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(forward, reverse),
    ]
