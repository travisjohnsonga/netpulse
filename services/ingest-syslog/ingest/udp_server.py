"""
Async UDP syslog receiver (RFC 3164 / RFC 5424 over UDP).

One UDP datagram == one syslog message.  The protocol creates an asyncio
task per datagram so the event loop is never blocked.
"""
import asyncio
import logging

from .parser import parse
from .publisher import NATSPublisher

logger = logging.getLogger(__name__)


class SyslogUDPProtocol(asyncio.DatagramProtocol):
    def __init__(self, publisher: NATSPublisher) -> None:
        self._publisher = publisher
        self._transport: asyncio.DatagramTransport | None = None

    def connection_made(self, transport: asyncio.DatagramTransport) -> None:  # type: ignore[override]
        self._transport = transport
        addr = transport.get_extra_info("sockname")
        logger.info("UDP syslog listening on %s:%d", addr[0], addr[1])

    def datagram_received(self, data: bytes, addr: tuple) -> None:
        source_ip, source_port = addr[0], addr[1]
        asyncio.create_task(self._handle(data, source_ip, source_port))

    async def _handle(self, data: bytes, source_ip: str, source_port: int) -> None:
        try:
            msg = parse(data, source_ip, source_port, "udp")
            await self._publisher.publish(msg)
        except Exception as exc:
            logger.error("error processing UDP datagram from %s: %s", source_ip, exc, exc_info=True)

    def error_received(self, exc: Exception) -> None:
        logger.warning("UDP socket error: %s", exc)

    def connection_lost(self, exc: Exception | None) -> None:
        if exc:
            logger.warning("UDP connection lost: %s", exc)
