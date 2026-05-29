import asyncio
import logging
import signal

from django.core.management.base import BaseCommand

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Watch NATS for device config events; evaluate compliance policies and persist results."

    def handle(self, *args, **options):
        asyncio.run(self._run())

    async def _run(self):
        import nats
        from django.conf import settings

        logger.info("config-manager starting")
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
        await nc.subscribe("netpulse.config.>", cb=self._on_config)
        logger.info("config-manager subscribed to netpulse.config.>")

        await stop_event.wait()
        await nc.drain()

    async def _on_config(self, msg):
        # TODO: deserialise config snapshot, run CompliancePolicyRule checks,
        # persist ComplianceResult rows, publish netpulse.compliance.result
        logger.debug("config event: subject=%s bytes=%d", msg.subject, len(msg.data))
