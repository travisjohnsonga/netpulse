"""
run_scheduler — periodic maintenance tasks.

Currently: purge resolved alerts past the retention window (default 90 days).
Runs the purge on startup then every --interval seconds.
"""
import logging
import signal
import time

from django.core.management.base import BaseCommand

logger = logging.getLogger(__name__)

ALERT_RETENTION_DAYS = 90
DEFAULT_INTERVAL_S = 24 * 3600  # daily


class Command(BaseCommand):
    help = "Run periodic maintenance tasks (resolved-alert purge, ...)."

    def add_arguments(self, parser):
        parser.add_argument("--interval", type=int, default=DEFAULT_INTERVAL_S)
        parser.add_argument("--once", action="store_true")

    def handle(self, *args, **options):
        stop = {"flag": False}

        def _shutdown(*_):
            stop["flag"] = True
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                signal.signal(sig, _shutdown)
            except ValueError:
                pass

        logger.info("scheduler started (interval=%ss)", options["interval"])
        while not stop["flag"]:
            self._cycle()
            if options["once"]:
                return
            slept = 0
            while slept < options["interval"] and not stop["flag"]:
                time.sleep(min(5, options["interval"] - slept))
                slept += 5
        logger.info("scheduler stopped")

    def _cycle(self):
        try:
            from apps.alerts.management.commands.purge_resolved_alerts import purge_resolved_alerts
            n = purge_resolved_alerts(ALERT_RETENTION_DAYS)
            if n:
                logger.info("scheduler: purged %d resolved alerts (>%dd)", n, ALERT_RETENTION_DAYS)
        except Exception as exc:
            logger.error("scheduler: alert purge failed: %s", exc)
