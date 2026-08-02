from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('alerts', '0005_notificationlog'),
    ]

    operations = [
        migrations.AddField(
            model_name='alertrule',
            name='notify_enabled',
            field=models.BooleanField(default=True),
        ),
    ]
