import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('compliance', '0002_initial'),
        ('devices', '0020_device_role'),
        ('configbackup', '0001_initial'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='ComplianceTemplate',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=128)),
                ('description', models.TextField(blank=True)),
                ('platform', models.CharField(blank=True, help_text='e.g. ios_xe, aos_cx', max_length=50)),
                ('template_content', models.TextField(help_text='Jinja2 template defining expected config lines')),
                ('variables', models.JSONField(blank=True, default=dict, help_text='Default Jinja2 variables')),
                ('enabled', models.BooleanField(default=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
                ('role', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='compliance_templates', to='devices.devicerole')),
                ('site', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='compliance_templates', to='devices.site')),
            ],
            options={
                'ordering': ['name'],
            },
        ),
        migrations.CreateModel(
            name='ComplianceTemplateResult',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('status', models.CharField(choices=[('compliant', 'Compliant'), ('non_compliant', 'Non-Compliant'), ('error', 'Error'), ('skipped', 'Skipped')], db_index=True, max_length=20)),
                ('score', models.FloatField(help_text='0.0-100.0 compliance %', null=True)),
                ('checked_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('findings', models.JSONField(default=list, help_text='List of compliance findings')),
                ('missing_count', models.IntegerField(default=0)),
                ('extra_count', models.IntegerField(default=0)),
                ('drift_count', models.IntegerField(default=0)),
                ('remediation', models.TextField(blank=True, help_text='Config commands to remediate')),
                ('config_snapshot', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to='configbackup.deviceconfig')),
                ('device', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='template_compliance_results', to='devices.device')),
                ('template', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='results', to='compliance.compliancetemplate')),
            ],
            options={
                'ordering': ['-checked_at'],
                'indexes': [models.Index(fields=['device', 'template', '-checked_at'], name='compliance__device__8928d3_idx')],
            },
        ),
        migrations.CreateModel(
            name='DeviceComplianceOverride',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('variables', models.JSONField(default=dict, help_text='Override template variables for this specific device')),
                ('device', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='compliance_overrides', to='devices.device')),
                ('template', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='device_overrides', to='compliance.compliancetemplate')),
            ],
        ),
        migrations.AddConstraint(
            model_name='devicecomplianceoverride',
            constraint=models.UniqueConstraint(fields=('device', 'template'), name='unique_device_template_override'),
        ),
    ]
