from django.apps import AppConfig


class ConfigBackupConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.configbackup"
    label = "configbackup"
    verbose_name = "Configuration Backup"
