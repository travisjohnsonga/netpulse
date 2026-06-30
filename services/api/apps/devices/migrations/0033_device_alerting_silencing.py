from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('devices', '0032_backfill_device_kind'),
    ]

    operations = [
        migrations.AddField(
            model_name='device',
            name='alerting_enabled',
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name='device',
            name='silenced_until',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
