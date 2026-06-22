from django.db import migrations


def seed_forwards(apps, schema_editor):
    from apps.config_templates.defaults import seed_builtin_templates
    ConfigPushTemplate = apps.get_model("config_templates", "ConfigPushTemplate")
    seed_builtin_templates(ConfigPushTemplate)


def seed_backwards(apps, schema_editor):
    ConfigPushTemplate = apps.get_model("config_templates", "ConfigPushTemplate")
    ConfigPushTemplate.objects.filter(builtin=True).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("config_templates", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed_forwards, seed_backwards),
    ]
