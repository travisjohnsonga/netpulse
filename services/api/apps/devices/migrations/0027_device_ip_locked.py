from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("devices", "0026_device_mac_address"),
    ]

    operations = [
        migrations.AddField(
            model_name="device",
            name="ip_locked",
            field=models.BooleanField(default=False),
        ),
    ]
