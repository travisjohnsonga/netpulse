# Generated for AgentCertAuthentication performance fix.

from django.db import migrations, models


def _normalize(serial: str) -> str:
    return (serial or "").replace(":", "").replace(" ", "").strip().upper()


def backfill_normalized_serial(apps, schema_editor):
    Agent = apps.get_model("agents", "Agent")
    for agent in Agent.objects.exclude(cert_serial="").only("pk", "cert_serial"):
        normalized = _normalize(agent.cert_serial)
        Agent.objects.filter(pk=agent.pk).update(cert_serial_normalized=normalized)


class Migration(migrations.Migration):

    dependencies = [
        ('agents', '0004_agent_reported_services_agentrole_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='agent',
            name='cert_serial_normalized',
            field=models.CharField(blank=True, db_index=True, default='', max_length=128),
            preserve_default=False,
        ),
        migrations.RunPython(backfill_normalized_serial, migrations.RunPython.noop),
    ]
