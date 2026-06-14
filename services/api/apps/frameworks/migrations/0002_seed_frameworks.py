"""Seed the framework + control catalog (idempotent) so reports work on first boot."""
from django.db import migrations


def seed(apps, schema_editor):
    from apps.frameworks.management.commands.seed_frameworks import FRAMEWORKS
    RegulatoryFramework = apps.get_model("frameworks", "RegulatoryFramework")
    FrameworkControl = apps.get_model("frameworks", "FrameworkControl")
    for key, (name, version, desc, controls) in FRAMEWORKS.items():
        fw, _ = RegulatoryFramework.objects.update_or_create(
            key=key, defaults={"name": name, "version": version, "description": desc})
        for control_id, title, category, mapping_key in controls:
            FrameworkControl.objects.update_or_create(
                framework=fw, control_id=control_id,
                defaults={"title": title, "category": category, "mapping_key": mapping_key})


def unseed(apps, schema_editor):
    RegulatoryFramework = apps.get_model("frameworks", "RegulatoryFramework")
    RegulatoryFramework.objects.all().delete()


class Migration(migrations.Migration):
    dependencies = [("frameworks", "0001_initial")]
    operations = [migrations.RunPython(seed, unseed)]
