"""
run_scheduler — periodic maintenance tasks, each on its own cadence.

Wakes every --tick seconds and runs any task whose interval has elapsed:
  - resolved-alert purge — daily (run on startup)
  - ARP/MAC table collection — every 6h (first run one interval after startup,
    so a scheduler restart doesn't SSH the whole fleet immediately)

ARP/MAC cadence is overridable via ARP_MAC_COLLECT_INTERVAL_S.
"""
import logging
import os
import signal
import time

from django.core.management.base import BaseCommand

logger = logging.getLogger(__name__)

ALERT_RETENTION_DAYS = 90
ALERT_PURGE_INTERVAL_S = 24 * 3600  # daily
ARP_MAC_INTERVAL_S = int(os.environ.get("ARP_MAC_COLLECT_INTERVAL_S", str(6 * 3600)))
DEFAULT_TICK_S = 300


class Command(BaseCommand):
    help = "Run periodic maintenance tasks (alert purge, ARP/MAC collection, ...)."

    def add_arguments(self, parser):
        # --interval kept for backwards compatibility (alert-purge cadence).
        parser.add_argument("--interval", type=int, default=ALERT_PURGE_INTERVAL_S)
        parser.add_argument("--tick", type=int, default=DEFAULT_TICK_S)
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

        # (name, interval_s, fn, run_on_start) — last_run in monotonic seconds.
        tasks = [
            ["alert_purge", options["interval"], self._purge_alerts, True, None],
            ["arp_mac", ARP_MAC_INTERVAL_S, self._collect_arp_mac, False, None],
        ]
        now = time.monotonic()
        for t in tasks:
            # run_on_start=False → schedule the first run one interval out.
            t[4] = None if t[3] else now

        tick = max(5, options["tick"])
        logger.info("scheduler started (tick=%ss, alert_purge=%ss, arp_mac=%ss)",
                    tick, options["interval"], ARP_MAC_INTERVAL_S)
        while not stop["flag"]:
            now = time.monotonic()
            for t in tasks:
                name, interval, fn, _ros, last = t
                if last is None or (now - last) >= interval:
                    try:
                        fn()
                    except Exception as exc:
                        logger.error("scheduler: task %s failed: %s", name, exc)
                    t[4] = now
            if options["once"]:
                return
            slept = 0
            while slept < tick and not stop["flag"]:
                time.sleep(min(5, tick - slept))
                slept += 5
        logger.info("scheduler stopped")

    def _purge_alerts(self):
        from apps.alerts.management.commands.purge_resolved_alerts import purge_resolved_alerts
        n = purge_resolved_alerts(ALERT_RETENTION_DAYS)
        if n:
            logger.info("scheduler: purged %d resolved alerts (>%dd)", n, ALERT_RETENTION_DAYS)

    def _collect_arp_mac(self):
        from django.core.management import call_command
        logger.info("scheduler: collecting ARP/MAC tables")
        call_command("collect_arp_mac", all=True)
