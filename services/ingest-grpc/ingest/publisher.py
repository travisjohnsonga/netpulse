"""
Async NATS JetStream publisher for parsed gNMI telemetry notifications.
"""
import json
import logging

import nats
import nats.js.errors

logger = logging.getLogger(__name__)


class NATSPublisher:
    """
    Maintains a single async NATS connection and JetStream context.
    Thread-safety: designed for a single async event loop.
    """

    def __init__(
        self,
        url: str,
        user: str,
        password: str,
        subject_prefix: str,
        stream_name: str,
        stream_max_age_seconds: int,
    ) -> None:
        self._url = url
        self._user = user
        self._password = password
        self._subject_prefix = subject_prefix
        self._stream_name = stream_name
        self._stream_max_age_ns = stream_max_age_seconds * 1_000_000_000

        self._nc: nats.NATS | None = None
        self._js = None

    async def connect(self) -> None:
        self._nc = await nats.connect(
            self._url,
            user=self._user,
            password=self._password,
        )
        self._js = self._nc.jetstream()
        await self._ensure_stream()
        logger.info("NATS publisher connected: url=%s stream=%s", self._url, self._stream_name)

    async def _ensure_stream(self) -> None:
        try:
            await self._js.stream_info(self._stream_name)
            logger.debug("JetStream stream %r already exists", self._stream_name)
        except nats.js.errors.NotFoundError:
            await self._js.add_stream(
                name=self._stream_name,
                subjects=[f"{self._subject_prefix}.>"],
            )
            logger.info(
                "created JetStream stream %r covering %s.>",
                self._stream_name,
                self._subject_prefix,
            )

    async def publish_notification(self, notification_dict: dict) -> None:
        """
        Serialise a parsed notification dict as JSON and publish to JetStream.

        Subject: <prefix>.<target_sanitised>
        where target_sanitised replaces '.' and ':' with '-' and strips brackets.
        """
        raw_target = notification_dict.get("target", "unknown")
        # Sanitise for use as a NATS subject token (no dots, colons, or spaces)
        target_token = (
            raw_target.replace(".", "-").replace(":", "-").replace(" ", "_").strip("[]")
        )
        subject = f"{self._subject_prefix}.{target_token}"
        payload = json.dumps(notification_dict, separators=(",", ":")).encode()

        try:
            ack = await self._js.publish(subject, payload)
            logger.debug(
                "published to %s seq=%d bytes=%d", subject, ack.seq, len(payload)
            )
        except Exception as exc:
            # Log and drop; a production implementation would buffer and retry.
            logger.error("failed to publish to %s: %s", subject, exc)

    async def drain(self) -> None:
        if self._nc and not self._nc.is_closed:
            await self._nc.drain()
            logger.info("NATS connection drained")
