from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("alerts", "0008_rename_notificationlog_indexes"),
    ]

    operations = [
        migrations.AddField(
            model_name="alertrule",
            name="created_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="created_alert_rules",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]
