import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('devices', '0020_device_role'),
    ]

    operations = [
        migrations.CreateModel(
            name='HostnameRule',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('name', models.CharField(max_length=128)),
                ('pattern', models.CharField(help_text='Regex pattern to match hostname', max_length=255)),
                ('rule_type', models.CharField(choices=[('role', 'Role'), ('site', 'Site'), ('both', 'Role + Site')], default='role', max_length=10)),
                ('priority', models.IntegerField(default=100, help_text='Lower = higher priority. First match wins.')),
                ('enabled', models.BooleanField(default=True)),
                ('role', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='hostname_rules', to='devices.devicerole')),
                ('site', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='hostname_rules', to='devices.site')),
            ],
            options={
                'ordering': ['priority', 'name'],
                'abstract': False,
            },
        ),
    ]
