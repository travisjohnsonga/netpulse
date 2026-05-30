import logging

from django.conf import settings
from django.core.management.base import BaseCommand

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Load community advisory YAML (Juniper/Arista/…) and correlate to devices."

    def add_arguments(self, parser):
        parser.add_argument(
            "--path", default=None,
            help="Advisories directory (defaults to settings.COMMUNITY_ADVISORIES_DIR).",
        )

    def handle(self, *args, **options):
        from apps.cve import community

        directory = options["path"] or getattr(settings, "COMMUNITY_ADVISORIES_DIR", "/app/advisories")
        summary = community.sync_advisories(directory)
        self.stdout.write(self.style.SUCCESS(
            f"community advisories: {summary['advisories']} parsed, "
            f"{summary['cves_upserted']} CVEs upserted, "
            f"{summary['device_links']} device link(s), {summary['skipped']} skipped"
        ))
