from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("collectors", "0002_collector_collector_ip_collector_is_default_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="collector",
            name="collector_type",
            field=models.CharField(
                choices=[("local", "Local Server"), ("remote", "Remote Agent")],
                db_index=True,
                default="remote",
                max_length=10,
            ),
        ),
        migrations.AddField(
            model_name="collector",
            name="hostname",
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddField(
            model_name="collector",
            name="location",
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddField(
            model_name="collector",
            name="capabilities",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
