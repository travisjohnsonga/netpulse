import logging
import time

from django.core.management.base import BaseCommand

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Alert engine (stub) — evaluates AlertRules against events (not yet implemented)."

    def handle(self, *args, **options):
        # Stub: stay alive quietly until stopped, instead of exiting (which made
        # the container restart-loop under restart:unless-stopped). NOTE: alert
        # *dispatch* no longer "lands later" — it is wired via the AlertEvent
        # post_save signal (apps/alerts/signals.py → dispatch.py), which is
        # connected in every process that writes AlertEvents (reachability
        # monitor, check engine, scheduler, stream-processor, …), so delivery
        # happens inline at the fire/resolve point. What remains for this engine
        # is future rule-condition *evaluation* (turning raw events into alerts).
        logger.info("run_alert_engine: starting (stub)")
        try:
            while True:
                time.sleep(60)
        except KeyboardInterrupt:
            logger.info("run_alert_engine: shutting down")
