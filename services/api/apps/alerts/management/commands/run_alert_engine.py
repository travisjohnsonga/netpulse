import asyncio
import logging
import signal

from django.core.management.base import BaseCommand

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Evaluate AlertRules against incoming NATS events and dispatch notifications."

    def handle(self, *args, **options):
        asyncio.run(self._run())

    async def _run(self):
        import nats
        from django.conf import settings

        logger.info("alert-engine starting")
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
        await nc.subscribe("netpulse.events.>", cb=self._on_event)
        await nc.subscribe("netpulse.compliance.result", cb=self._on_event)
        logger.info("alert-engine subscribed")

        await stop_event.wait()
        await nc.drain()

    async def _on_event(self, msg):
        # TODO: load active AlertRules, evaluate conditions, create AlertEvent rows,
        # dispatch to configured AlertChannels (Slack/email/PagerDuty), respect cooldowns
        logger.debug("alert event: subject=%s bytes=%d", msg.subject, len(msg.data))
