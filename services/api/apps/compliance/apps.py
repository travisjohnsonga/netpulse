from django.apps import AppConfig


class ComplianceConfig(AppConfig):
    name = "apps.compliance"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        from . import signals  # noqa: F401  (register OS-inventory refresh signals)
