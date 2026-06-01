from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("devices", "0013_discoveryjob_progress"),
    ]

    operations = [
        migrations.AddField(
            model_name="discoveryjob",
            name="cancel_requested",
            field=models.BooleanField(default=False),
        ),
    ]
