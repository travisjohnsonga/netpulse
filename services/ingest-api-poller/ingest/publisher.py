"""
NATS JetStream publisher for vendor API data.

Subjects:
  netpulse.vendor.<vendor>.<integration_id>.devices  — device inventory/status
  netpulse.vendor.<vendor>.<integration_id>.alerts   — alerts/events
  netpulse.vendor.<vendor>.<integration_id>.metrics  — time-series metrics
"""
import json
import logging
import re
from typing import Any

import nats
import nats.js.errors

from .models import VendorAlert, VendorDevice, VendorMetric

logger = logging.getLogger(__name__)

_INVALID_TOKEN_RE = re.compile(r"[^a-zA-Z0-9.\-_]")


def _token(s: str) -> str:
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

    async def publish_device(self, device: VendorDevice) -> None:
        subject = (
            f"{self._prefix}.{_token(device.vendor)}"
            f".{_token(device.integration_id)}.devices"
        )
        await self._pub(subject, device.to_dict())

    async def publish_alert(self, alert: VendorAlert) -> None:
        subject = (
            f"{self._prefix}.{_token(alert.vendor)}"
            f".{_token(alert.integration_id)}.alerts"
        )
        await self._pub(subject, alert.to_dict())

    async def publish_metric(self, metric: VendorMetric) -> None:
        subject = (
            f"{self._prefix}.{_token(metric.vendor)}"
            f".{_token(metric.integration_id)}.metrics"
        )
        await self._pub(subject, metric.to_dict())

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
