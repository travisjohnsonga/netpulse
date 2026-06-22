from django.core.management.base import BaseCommand

from apps.config_templates.defaults import seed_builtin_templates
from apps.config_templates.models import ConfigPushTemplate


class Command(BaseCommand):
    help = "Seed the built-in config-push templates (idempotent)."

    def handle(self, *args, **options):
        created = seed_builtin_templates(ConfigPushTemplate)
        self.stdout.write(self.style.SUCCESS(
            f"Config-push templates seeded ({created} created)."))
