"""
NATS JetStream publisher for OTLP telemetry signals.

Subjects:
  netpulse.otel.<exporter_token>.metrics  — metric export batches
  netpulse.otel.<exporter_token>.logs     — log record batches
  netpulse.otel.<exporter_token>.traces   — span batches
"""
import json
import logging
import re
from typing import Any

import nats
import nats.js.errors

logger = logging.getLogger(__name__)

_INVALID_TOKEN_RE = re.compile(r"[^a-zA-Z0-9.\-_]")


def _token(s: str) -> str:
    """Sanitise a string for use as a NATS subject token."""
    return _INVALID_TOKEN_RE.sub("_", s or "unknown") or "unknown"


class NATSPublisher:
    def __init__(
        self,
        url: str,
        user: str,
        password: str,
        prefix: str,
        stream_name: str,
        stream_max_age_seconds: int,
    ) -> None:
        self._url = url
        self._user = user
        self._password = password
        self._prefix = prefix
        self._stream_name = stream_name
        self._max_age_ns = stream_max_age_seconds * 1_000_000_000
        self._nc: nats.NATS | None = None
        self._js = None

    @property
    def nc(self):
        return self._nc

    async def connect(self) -> None:
        self._nc = await nats.connect(self._url, user=self._user, password=self._password)
        self._js = self._nc.jetstream()
        await self._ensure_stream()
        logger.info("NATS publisher connected: url=%s stream=%s", self._url, self._stream_name)

    async def _ensure_stream(self) -> None:
        try:
            await self._js.stream_info(self._stream_name)
        except nats.js.errors.NotFoundError:
            await self._js.add_stream(
                name=self._stream_name,
                subjects=[f"{self._prefix}.>"],
                max_age=self._max_age_ns,
            )
            logger.info("created JetStream stream %r", self._stream_name)

    async def publish_metrics(self, exporter_ip: str, payload: dict[str, Any]) -> None:
        subject = f"{self._prefix}.{_token(exporter_ip)}.metrics"
        await self._pub(subject, payload)

    async def publish_logs(self, exporter_ip: str, payload: dict[str, Any]) -> None:
        subject = f"{self._prefix}.{_token(exporter_ip)}.logs"
        await self._pub(subject, payload)

    async def publish_traces(self, exporter_ip: str, payload: dict[str, Any]) -> None:
        subject = f"{self._prefix}.{_token(exporter_ip)}.traces"
        await self._pub(subject, payload)

    async def _pub(self, subject: str, payload: dict[str, Any]) -> None:
        data = json.dumps(payload, separators=(",", ":"), default=str).encode()
        try:
            ack = await self._js.publish(subject, data)
            logger.debug("published seq=%d to %s (%d bytes)", ack.seq, subject, len(data))
        except Exception as exc:
            logger.error("publish failed for %s: %s", subject, exc)

    async def drain(self) -> None:
        if self._nc and not self._nc.is_closed:
            await self._nc.drain()
            logger.info("NATS connection drained")
