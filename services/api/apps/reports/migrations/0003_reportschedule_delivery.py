# Generated for the store-only / no-email report delivery feature.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('reports', '0002_alter_reportschedule_frequency'),
    ]

    operations = [
        # default='email' so existing schedules (which have recipients and were
        # email-delivered) keep emailing unchanged — back-compat.
        migrations.AddField(
            model_name='reportschedule',
            name='delivery',
            field=models.CharField(
                choices=[('email', 'Email'), ('store_only', 'Store only'), ('both', 'Email + Store')],
                default='email', help_text='email | store_only | both', max_length=16),
        ),
        migrations.AlterField(
            model_name='reportschedule',
            name='recipients',
            field=models.JSONField(
                default=list, help_text='Email addresses (required for email/both)'),
        ),
    ]
