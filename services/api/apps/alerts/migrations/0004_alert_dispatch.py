from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('alerts', '0003_alertrule_is_system'),
    ]

    operations = [
        migrations.AddField(
            model_name='alertevent',
            name='fired_notified_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='alertevent',
            name='resolved_notified_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name='alertchannel',
            name='channel_type',
            field=models.CharField(
                choices=[
                    ('slack', 'Slack'),
                    ('email', 'Email'),
                    ('pagerduty', 'PagerDuty'),
                    ('webhook', 'Webhook'),
                    ('teams', 'Microsoft Teams'),
                ],
                max_length=20,
            ),
        ),
    ]
