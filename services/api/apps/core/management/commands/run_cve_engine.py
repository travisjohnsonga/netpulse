from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "run_cve_engine — not yet implemented (stub)"

    def handle(self, *args, **options):
        self.stdout.write(self.style.WARNING("run_cve_engine: not yet implemented"))
