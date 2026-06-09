from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("integrations", "0005_unificloudaccount_unificontroller_cloud_host_id"),
    ]

    operations = [
        migrations.AddField(
            model_name="unificontroller",
            name="model",
            field=models.CharField(blank=True, max_length=128),
        ),
        migrations.AddField(
            model_name="unificontroller",
            name="version",
            field=models.CharField(blank=True, max_length=64),
        ),
    ]
