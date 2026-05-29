import asyncio
import logging
import signal

from django.core.management.base import BaseCommand

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Recompute DeviceRiskScore for each device from CVE, compliance, lifecycle, and anomaly sub-scores."

    def handle(self, *args, **options):
        asyncio.run(self._run())

    async def _run(self):
        import nats
        from django.conf import settings

        logger.info("security-engine starting")
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
        # Recompute whenever any sub-score input changes
        for subject in ("netpulse.cve.updated", "netpulse.compliance.result", "netpulse.lifecycle.check"):
            await nc.subscribe(subject, cb=self._on_score_input)
        logger.info("security-engine subscribed")

        await stop_event.wait()
        await nc.drain()

    async def _on_score_input(self, msg):
        # TODO: identify affected device(s), aggregate sub-scores,
        # upsert DeviceRiskScore, publish netpulse.security.score_updated
        logger.debug("score input: subject=%s bytes=%d", msg.subject, len(msg.data))
