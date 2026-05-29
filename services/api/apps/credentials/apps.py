from django.apps import AppConfig


class CredentialsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.credentials"
    label = "credentials"
    verbose_name = "Credential Profiles"
