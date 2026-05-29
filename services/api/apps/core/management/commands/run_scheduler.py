import logging

from django.core.management.base import BaseCommand

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Scheduler — triggers periodic platform jobs (CVE fetch, lifecycle check, discovery) (stub)"

    def handle(self, *args, **options):
        self.stdout.write("scheduler: not yet implemented")
