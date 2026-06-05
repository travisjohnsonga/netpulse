import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='LogFilter',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=128)),
                ('pattern', models.TextField(help_text='Regular expression pattern')),
                ('action', models.CharField(choices=[('suppress', 'Suppress'), ('highlight', 'Highlight'), ('tag', 'Tag')], default='suppress', max_length=20)),
                ('color', models.CharField(blank=True, help_text='Hex color for highlight action', max_length=7)),
                ('tag', models.CharField(blank=True, max_length=64)),
                ('platforms', models.JSONField(blank=True, default=list, help_text='Empty = all platforms')),
                ('enabled', models.BooleanField(default=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['name'],
            },
        ),
    ]
