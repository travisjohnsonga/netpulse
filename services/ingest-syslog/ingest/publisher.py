"""
NATS JetStream publisher for parsed syslog messages.
"""
import json
import logging
import re

import nats
import nats.js.errors

logger = logging.getLogger(__name__)

# Only characters allowed bare inside a NATS subject token; everything else
# is replaced with '_'. Note: '.' is deliberately kept so that FQDNs like
# router1.example.com produce the natural subject hierarchy .router1.example.com
_INVALID_TOKEN_RE = re.compile(r"[^a-zA-Z0-9.\-_]")


def _sanitise_token(s: str) -> str:
    token = _INVALID_TOKEN_RE.sub("_", s or "unknown").strip(".")
    return token or "unknown"


class NATSPublisher:
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
        self._nc = await nats.connect(self._url, user=self._user, password=self._password)
        self._js = self._nc.jetstream()
        await self._ensure_stream()
        logger.info(
            "NATS publisher connected: url=%s stream=%s prefix=%s",
            self._url, self._stream_name, self._subject_prefix,
        )

    async def _ensure_stream(self) -> None:
        try:
            await self._js.stream_info(self._stream_name)
        except nats.js.errors.NotFoundError:
            await self._js.add_stream(
                name=self._stream_name,
                subjects=[f"{self._subject_prefix}.>"],
                max_age=self._stream_max_age_ns,
            )
            logger.info("created JetStream stream %r", self._stream_name)

    async def publish(self, message: dict) -> None:
        hostname = message.get("hostname") or message.get("source_ip") or "unknown"
        subject = f"{self._subject_prefix}.{_sanitise_token(hostname)}"
        payload = json.dumps(message, separators=(",", ":"), default=str).encode()
        try:
            ack = await self._js.publish(subject, payload)
            logger.debug("published seq=%d to %s (%d bytes)", ack.seq, subject, len(payload))
        except Exception as exc:
            logger.error("publish failed for %s: %s", subject, exc)

    async def drain(self) -> None:
        if self._nc and not self._nc.is_closed:
            await self._nc.drain()
            logger.info("NATS connection drained")
