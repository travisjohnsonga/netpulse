import logging
import time

from django.core.management.base import BaseCommand

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Security engine (stub) — recomputes DeviceRiskScore (not yet implemented)."

    def handle(self, *args, **options):
        # Stub: stay alive quietly until stopped, instead of exiting (which made
        # the container restart-loop under restart:unless-stopped). Real risk
        # recomputation lands later.
        logger.info("run_security_engine: starting (stub)")
        try:
            while True:
                time.sleep(60)
        except KeyboardInterrupt:
            logger.info("run_security_engine: shutting down")
