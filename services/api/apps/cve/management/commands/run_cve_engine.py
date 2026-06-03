import logging
import time

from django.conf import settings
from django.core.management.base import BaseCommand

from apps.cve import sync
from apps.cve.models import CVE

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        "CVE engine — fetches CVEs from NVD (+ optional Cisco PSIRT / CISA KEV) "
        "for the platforms in inventory and correlates them to devices."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--once", action="store_true",
            help="Run a single sync and exit (default: loop every CVE_SYNC_INTERVAL_HOURS).",
        )

    def handle(self, *args, **options):
        interval = getattr(settings, "CVE_SYNC_INTERVAL_HOURS", 24) * 3600

        if options["once"]:
            logger.info("run_cve_engine: single sync (--once)")
            sync.run_sync()
            return

        logger.info("run_cve_engine: starting (interval %sh)", interval // 3600)

        # On startup, sync immediately if we have no CVE data yet; otherwise wait
        # for the next interval so a restart loop doesn't hammer NVD.
        first_delay = 0 if not CVE.objects.exists() else interval
        if first_delay:
            logger.info("CVE data present — first sync in %sh", first_delay // 3600)

        try:
            next_run = time.monotonic() + first_delay
            while True:
                if time.monotonic() >= next_run:
                    try:
                        sync.run_sync()
                    except Exception:
                        logger.exception("CVE sync error (continuing)")
                    next_run = time.monotonic() + interval
                time.sleep(min(60, max(1, next_run - time.monotonic())))
        except KeyboardInterrupt:
            logger.info("run_cve_engine: shutting down")
