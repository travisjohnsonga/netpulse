from django.apps import AppConfig


class AlertsConfig(AppConfig):
    name = "apps.alerts"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        # Connect the fire/resolve → dispatch signal handler.
        from . import signals  # noqa: F401
