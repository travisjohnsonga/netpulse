from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("devices", "0012_discoveryjob_credential_profile"),
    ]

    operations = [
        migrations.AddField(
            model_name="discoveryjob",
            name="progress_current",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="discoveryjob",
            name="progress_total",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="discoveryjob",
            name="progress_message",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.AddField(
            model_name="discoveryjob",
            name="ips_scanned",
            field=models.PositiveIntegerField(default=0),
        ),
    ]
