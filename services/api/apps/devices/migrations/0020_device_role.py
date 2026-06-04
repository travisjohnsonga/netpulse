import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('devices', '0019_discoveryjob_site'),
    ]

    operations = [
        migrations.CreateModel(
            name='DeviceRole',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('name', models.CharField(max_length=64, unique=True)),
                ('slug', models.SlugField(blank=True, max_length=64, unique=True)),
                ('color', models.CharField(default='#6366f1', max_length=7)),
                ('description', models.CharField(blank=True, max_length=255)),
                ('icon', models.CharField(blank=True, max_length=50)),
            ],
            options={
                'ordering': ['name'],
                'abstract': False,
            },
        ),
        migrations.AddField(
            model_name='device',
            name='role',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='devices', to='devices.devicerole'),
        ),
    ]
