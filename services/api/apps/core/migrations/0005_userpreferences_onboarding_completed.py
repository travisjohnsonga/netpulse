from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0004_systemsetting"),
    ]

    operations = [
        migrations.AddField(
            model_name="userpreferences",
            name="onboarding_completed",
            field=models.BooleanField(default=False),
        ),
    ]
