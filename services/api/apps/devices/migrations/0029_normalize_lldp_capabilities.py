"""Re-normalise existing LLDPNeighbor.capabilities through the (now extended)
canonical capability normaliser, so records collected before ``wlan``/``tel``/…
folding stored raw tokens (e.g. ``["bridge", "wlan"]``) become canonical
(``["bridge", "wlan-ap"]``) and capability rules/filters match them.
"""
from django.db import migrations


def normalize_existing(apps, schema_editor):
    from apps.devices.lldp import normalize_capabilities

    LLDPNeighbor = apps.get_model("devices", "LLDPNeighbor")
    updated = 0
    for nb in LLDPNeighbor.objects.exclude(capabilities=[]).iterator():
        caps = nb.capabilities or []
        new_caps = normalize_capabilities(caps)
        if new_caps != caps:
            nb.capabilities = new_caps
            nb.save(update_fields=["capabilities"])
            updated += 1
    if updated:
        print(f"  normalized capabilities on {updated} LLDP neighbor(s)")


def noop(apps, schema_editor):
    # Normalisation is not reversible (raw spelling is lost); no-op on reverse.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("devices", "0028_alter_device_platform"),
    ]

    operations = [
        migrations.RunPython(normalize_existing, noop),
    ]
