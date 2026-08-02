import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('alerts', '0004_alert_dispatch'),
    ]

    operations = [
        migrations.CreateModel(
            name='NotificationLog',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('channel_name', models.CharField(blank=True, max_length=255)),
                ('channel_type', models.CharField(max_length=20)),
                ('transition', models.CharField(max_length=10)),
                ('status', models.CharField(choices=[('sent', 'Sent'), ('failed', 'Failed')], db_index=True, max_length=8)),
                ('attempts', models.PositiveSmallIntegerField(default=1)),
                ('detail', models.TextField(blank=True)),
                ('channel', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='deliveries', to='alerts.alertchannel')),
                ('event', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='deliveries', to='alerts.alertevent')),
            ],
            options={
                'ordering': ['-created_at'],
                'abstract': False,
            },
        ),
        migrations.AddIndex(
            model_name='notificationlog',
            index=models.Index(fields=['channel', '-created_at'], name='alerts_noti_channel_4d8f9e_idx'),
        ),
        migrations.AddIndex(
            model_name='notificationlog',
            index=models.Index(fields=['status', '-created_at'], name='alerts_noti_status_7c1a2b_idx'),
        ),
    ]
