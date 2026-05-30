import logging
import time

from django.core.management.base import BaseCommand

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Alert engine (stub) — evaluates AlertRules against events (not yet implemented)."

    def handle(self, *args, **options):
        # Stub: stay alive quietly until stopped, instead of exiting (which made
        # the container restart-loop under restart:unless-stopped). Real event
        # evaluation/dispatch lands later.
        logger.info("run_alert_engine: starting (stub)")
        try:
            while True:
                time.sleep(60)
        except KeyboardInterrupt:
            logger.info("run_alert_engine: shutting down")
