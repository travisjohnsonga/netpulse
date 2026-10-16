# Generated for seed-once bootstrap markers (rule-management arc).

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0019_alter_auditlog_event_type'),
    ]

    operations = [
        migrations.CreateModel(
            name='SeedMarker',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('seed_key', models.CharField(db_index=True, max_length=128, unique=True)),
                ('note', models.CharField(blank=True, max_length=256)),
            ],
            options={
                'verbose_name': 'seed marker',
                'verbose_name_plural': 'seed markers',
                'ordering': ['-created_at'],
            },
        ),
    ]
