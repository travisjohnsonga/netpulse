from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("telemetry", "0004_monitoredinterface_alert_on_down_and_more"),
    ]

    operations = [
        migrations.AlterField(
            model_name="monitoredinterface",
            name="collection_method",
            field=models.CharField(
                choices=[
                    ("auto", "Auto"),
                    ("snmp", "SNMP"),
                    ("gnmi", "gNMI"),
                    ("rest", "REST API"),
                ],
                default="auto",
                max_length=8,
            ),
        ),
    ]
