from django.db import migrations


def backfill_device_kind(apps, schema_editor):
    """One-time classification of EXISTING devices using the legacy heuristic
    (agent-linked OR synthetic/loopback IP → server). After this runs, the
    heuristic is never used again — device_kind is set authoritatively at
    creation. Postgres-safe: agent linkage via the Agent model (not the reverse
    accessor), and only valid inet literals in the IP filter.

    Split out from 0031 (the AddField) so the column's index builds in its own
    migration before any data UPDATEs — see 0031's docstring (the deferred-index
    / pending-trigger-events problem)."""
    from django.db.models import Q

    Device = apps.get_model("devices", "Device")
    Agent = apps.get_model("agents", "Agent")
    SERVER = "server"

    # Agent-backed devices → server.
    agent_device_ids = list(
        Agent.objects.exclude(device__isnull=True).values_list("device_id", flat=True)
    )
    if agent_device_ids:
        Device.objects.filter(id__in=agent_device_ids).update(device_kind=SERVER)

    # Synthetic/loopback-IP devices (e.g. an agent link later cleared) → server.
    # Valid IP literals only (GenericIPAddressField → inet adaptation).
    synthetic = ["127.0.0.1", "::1", "0.0.0.0"]  # nosec B104 — synthetic IP literals to match in data, not a bind address
    Device.objects.filter(
        Q(ip_address__in=synthetic) | Q(management_ip__in=synthetic)
    ).update(device_kind=SERVER)


def noop_reverse(apps, schema_editor):
    """Reverse is a no-op — 0031's AddField reversal drops the column with its data."""


class Migration(migrations.Migration):

    dependencies = [
        ('devices', '0031_device_device_kind'),
        ('agents', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(backfill_device_kind, noop_reverse),
    ]
