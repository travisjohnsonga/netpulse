"""
Config-manager: collect device running configs and store snapshots.

Scheduled collection runs at fixed UTC windows (default 07:00 and 19:00 —
CONFIG_COLLECTION_HOUR_1 / _2), not on a short interval, so devices are polled
twice a day. Each scheduled run does change detection and raises a "Config
Changed" alert per device whose config changed (see apps.configbackup.tasks).

  python manage.py run_config_manager                 # daemon: collect at the windows
  python manage.py run_config_manager --once          # collect all active now, then exit
  python manage.py run_config_manager --device-id 5   # collect one device now (manual)

Set CONFIG_COLLECTION_ENABLED=false to disable the scheduled windows (the
daemon idles; --once / --device-id still work).
"""
import logging
import os
import signal
import threading
from datetime import datetime, timezone

from django.core.management.base import BaseCommand

logger = logging.getLogger(__name__)


def _collection_hours() -> set[int]:
    """Configured UTC collection hours (CONFIG_COLLECTION_HOUR_1 / _2)."""
    hours = set()
    for key, default in (("CONFIG_COLLECTION_HOUR_1", "7"), ("CONFIG_COLLECTION_HOUR_2", "19")):
        try:
            hours.add(int(os.environ.get(key, default)) % 24)
        except ValueError:
            pass
    return hours


def _enabled() -> bool:
    return os.environ.get("CONFIG_COLLECTION_ENABLED", "true").lower() != "false"


class Command(BaseCommand):
    help = "Collect device running configurations at scheduled UTC windows and store snapshots."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="Run one collection cycle then exit.")
        parser.add_argument("--device-id", default=None, help="Collect only this device (by id); treated as manual.")
        parser.add_argument("--check-interval", type=int, default=60,
                            help="How often (s) the daemon checks the clock for a collection window.")
        # Deprecated: collection is now at fixed UTC windows, not a fixed
        # interval. Accepted (ignored) so existing compose commands still start.
        parser.add_argument("--interval", type=int, default=None, help="(deprecated, ignored)")

    def handle(self, *args, **options):
        if options["once"] or options["device_id"] is not None:
            self._run_once(options["device_id"])
            return

        stop = threading.Event()
        signal.signal(signal.SIGTERM, lambda *_: stop.set())
        signal.signal(signal.SIGINT, lambda *_: stop.set())

        hours = sorted(_collection_hours())
        logger.info("config-manager starting (scheduled UTC hours=%s, enabled=%s)", hours, _enabled())
        last_run_window: tuple | None = None  # (date, hour) of the last scheduled run

        while not stop.is_set():
            if _enabled():
                now = datetime.now(timezone.utc)
                window = (now.date(), now.hour)
                if now.hour in _collection_hours() and window != last_run_window:
                    last_run_window = window
                    logger.info("config-manager: scheduled collection window %02d:00 UTC", now.hour)
                    from apps.configbackup.tasks import collect_all_configs
                    collect_all_configs()
            stop.wait(options["check_interval"])

        logger.info("config-manager stopped")

    @staticmethod
    def _run_once(device_id) -> None:
        from apps.configbackup.tasks import collect_all_configs, collect_device_config

        if device_id is not None:
            res = collect_device_config(device_id, collected_by="manual")
            logger.info("config-manager: manual collection of device %s → ok=%s", device_id, res.get("ok"))
        else:
            collect_all_configs()
