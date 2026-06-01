from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("credentials", "0003_delete_devicecredential"),
        ("devices", "0011_alter_topologylink_unique_together_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="discoveryjob",
            name="credential_profile",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="discovery_jobs",
                to="credentials.credentialprofile",
            ),
        ),
    ]
