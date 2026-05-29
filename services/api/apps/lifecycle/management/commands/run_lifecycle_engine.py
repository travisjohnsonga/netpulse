import asyncio
import logging
import signal

from django.core.management.base import BaseCommand

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Watch device inventory and emit alerts as lifecycle milestones approach."

    def handle(self, *args, **options):
        asyncio.run(self._run())

    async def _run(self):
        import nats
        from django.conf import settings

        logger.info("lifecycle-engine starting")
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
        await nc.subscribe("netpulse.lifecycle.check", cb=self._on_check)
        logger.info("lifecycle-engine subscribed to netpulse.lifecycle.check")

        await stop_event.wait()
        await nc.drain()

    async def _on_check(self, msg):
        # TODO: query LifecycleMilestone rows nearing their date,
        # publish netpulse.events.lifecycle for each approaching milestone
        logger.debug("lifecycle check triggered: subject=%s", msg.subject)
