import asyncio
import logging
import signal

from django.core.management.base import BaseCommand

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Periodically fetch CVE data from NVD/Cisco PSIRT and correlate with the device inventory."

    def handle(self, *args, **options):
        asyncio.run(self._run())

    async def _run(self):
        import nats
        from django.conf import settings

        logger.info("cve-engine starting")
        stop_event = asyncio.Event()

        def _shutdown(sig, _):
            stop_event.set()

        signal.signal(signal.SIGTERM, _shutdown)
        signal.signal(signal.SIGINT, _shutdown)

        nc = await nats.connect(
            settings.NATS_URL,
            user=settings.NATS_USER,
            password=settings.NATS_PASSWORD,
        )
        await nc.subscribe("netpulse.cve.fetch", cb=self._on_fetch_request)
        logger.info("cve-engine subscribed to netpulse.cve.fetch")

        await stop_event.wait()
        await nc.drain()

    async def _on_fetch_request(self, msg):
        # TODO: call NVD API (settings.NVD_API_KEY) and Cisco PSIRT API,
        # upsert CVE rows, correlate with Device.os_version/model,
        # create/update DeviceCVE rows, publish netpulse.cve.updated
        logger.debug("cve fetch request: subject=%s", msg.subject)
