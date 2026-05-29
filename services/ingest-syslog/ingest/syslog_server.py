"""
Entry point: python -m ingest.syslog_server

Starts both the UDP (RFC 3164/5424) and TCP (RFC 6587) syslog servers
concurrently in the same asyncio event loop.  All received messages are
parsed and published to NATS JetStream under netpulse.logs.<hostname>.
"""
import asyncio
import logging
import signal

from .config import cfg
from .publisher import NATSPublisher
from .tcp_server import handle_client
from .udp_server import SyslogUDPProtocol

logging.basicConfig(
    level=getattr(logging, cfg.log_level, logging.INFO),
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)


async def serve() -> None:
    publisher = NATSPublisher(
        url=cfg.nats_url,
        user=cfg.nats_user,
        password=cfg.nats_password,
        subject_prefix=cfg.subject_prefix,
        stream_name=cfg.stream_name,
        stream_max_age_seconds=cfg.stream_max_age_seconds,
    )
    await publisher.connect()

    loop = asyncio.get_running_loop()

    # ── UDP server ────────────────────────────────────────────────────────────
    try:
        udp_transport, _ = await loop.create_datagram_endpoint(
            lambda: SyslogUDPProtocol(publisher),
            local_addr=(cfg.host, cfg.udp_port),
        )
    except PermissionError:
        logger.error(
            "Cannot bind UDP port %d — needs NET_BIND_SERVICE capability or a port > 1024. "
            "Override SYSLOG_UDP_PORT in .env for local dev.",
            cfg.udp_port,
        )
        raise

    # ── TCP server ────────────────────────────────────────────────────────────
    try:
        tcp_server = await asyncio.start_server(
            lambda r, w: handle_client(r, w, publisher, cfg.tcp_max_line),
            cfg.host,
            cfg.tcp_port,
            limit=cfg.tcp_max_line,
        )
    except PermissionError:
        udp_transport.close()
        logger.error(
            "Cannot bind TCP port %d — needs NET_BIND_SERVICE capability or a port > 1024. "
            "Override SYSLOG_TCP_PORT in .env for local dev.",
            cfg.tcp_port,
        )
        raise

    addrs = ", ".join(str(s.getsockname()) for s in tcp_server.sockets)
    logger.info(
        "ingest-syslog running — UDP %s:%d  TCP %s",
        cfg.host, cfg.udp_port, addrs,
    )

    # ── Graceful shutdown ─────────────────────────────────────────────────────
    stop_event = asyncio.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop_event.set)

    await stop_event.wait()

    logger.info("shutdown signal received — draining...")
    udp_transport.close()
    tcp_server.close()
    await tcp_server.wait_closed()
    await publisher.drain()
    logger.info("ingest-syslog stopped")


def main() -> None:
    asyncio.run(serve())


if __name__ == "__main__":
    main()
