from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("devices", "0025_alter_device_platform"),
    ]

    operations = [
        migrations.AddField(
            model_name="device",
            name="mac_address",
            field=models.CharField(blank=True, db_index=True, max_length=17),
        ),
    ]
