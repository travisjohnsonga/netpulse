import asyncio
import logging
import signal

from django.core.management.base import BaseCommand

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Subscribe to NATS telemetry subjects and fan data out to InfluxDB, OpenSearch, and PostgreSQL."

    def handle(self, *args, **options):
        asyncio.run(self._run())

    async def _run(self):
        import nats
        from django.conf import settings

        logger.info("stream-processor starting")
        stop_event = asyncio.Event()

        def _shutdown(sig, _):
            logger.info("shutdown signal received")
            stop_event.set()

        signal.signal(signal.SIGTERM, _shutdown)
        signal.signal(signal.SIGINT, _shutdown)

        nc = await nats.connect(
            settings.NATS_URL,
            user=settings.NATS_USER,
            password=settings.NATS_PASSWORD,
        )
        logger.info("stream-processor connected to NATS at %s", settings.NATS_URL)

        await nc.subscribe("netpulse.telemetry.>", cb=self._on_telemetry)
        await nc.subscribe("netpulse.logs.>", cb=self._on_log)
        logger.info("stream-processor subscribed")

        await stop_event.wait()
        await nc.drain()
        logger.info("stream-processor stopped")

    async def _on_telemetry(self, msg):
        # TODO: parse msg.data (protobuf/JSON), write measurement to InfluxDB
        logger.debug("telemetry msg: subject=%s bytes=%d", msg.subject, len(msg.data))

    async def _on_log(self, msg):
        # TODO: parse msg.data (syslog/JSON), index document in OpenSearch
        logger.debug("log msg: subject=%s bytes=%d", msg.subject, len(msg.data))
