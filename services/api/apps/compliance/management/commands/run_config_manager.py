"""
Config-manager: poll active devices, collect running configs, store snapshots and
publish collection events to NATS.

  python manage.py run_config_manager                 # loop every 300s
  python manage.py run_config_manager --once          # one cycle, then exit
  python manage.py run_config_manager --device-id 5   # one device (manual)
  python manage.py run_config_manager --interval 600  # custom interval
"""
import logging
import signal
import threading

from django.core.management.base import BaseCommand

from apps.compliance import collector
from apps.devices.models import Device

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Collect device running configurations on a schedule and store snapshots."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="Run one collection cycle then exit.")
        parser.add_argument("--device-id", default=None, help="Collect only this device (by id); treated as manual.")
        parser.add_argument("--interval", type=int, default=300, help="Polling interval in seconds (default 300).")

    def handle(self, *args, **options):
        once = options["once"]
        device_id = options["device_id"]
        interval = options["interval"]

        stop = threading.Event()
        signal.signal(signal.SIGTERM, lambda *_: stop.set())
        signal.signal(signal.SIGINT, lambda *_: stop.set())

        logger.info("config-manager starting (interval=%ss, once=%s, device=%s)", interval, once, device_id or "all-active")

        while not stop.is_set():
            collected, failed = self._cycle(device_id)
            logger.info("cycle complete: %d collected, %d failed", collected, failed)
            if once:
                break
            # Interruptible sleep.
            stop.wait(interval)

        logger.info("config-manager stopped")

    def _cycle(self, device_id) -> tuple[int, int]:
        if device_id is not None:
            try:
                devices = list(Device.objects.filter(pk=device_id))
            except (ValueError, TypeError):
                logger.error("invalid --device-id %r", device_id)
                return (0, 0)
            collected_by = "manual"
            if not devices:
                logger.warning("device %s not found", device_id)
        else:
            devices = list(
                Device.objects.filter(status=Device.Status.ACTIVE).select_related("credential_profile")
            )
            collected_by = "scheduled"

        collected = failed = 0
        for device in devices:
            try:
                if collector.collect_one(device, collected_by) is not None:
                    collected += 1
                else:
                    failed += 1
            except Exception as exc:  # defensive — collect_one shouldn't raise
                failed += 1
                logger.error("unexpected error collecting %s: %s", device.hostname, exc)
        return (collected, failed)
