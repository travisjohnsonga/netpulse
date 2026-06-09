from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("integrations", "0010_migrate_unifi_credentials"),
    ]

    operations = [
        migrations.AddField(
            model_name="netboximport",
            name="verify_ssl",
            field=models.BooleanField(
                default=True,
                help_text=(
                    "Verify the NetBox server's SSL certificate. Disable only for "
                    "internal NetBox instances with self-signed certificates."
                ),
            ),
        ),
    ]
