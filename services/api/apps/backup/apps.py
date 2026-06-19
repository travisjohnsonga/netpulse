from django.apps import AppConfig


class BackupConfigApp(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.backup"
    label = "backup"
    verbose_name = "Platform Backup"
