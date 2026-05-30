from django.apps import AppConfig


class DevicesConfig(AppConfig):
    name = "apps.devices"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        from . import signals  # noqa: F401  (register SNMP publish signals)
