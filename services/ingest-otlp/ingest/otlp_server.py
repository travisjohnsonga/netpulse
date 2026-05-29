"""
Entry point: python -m ingest.otlp_server

Starts two concurrent subsystems:
  1. gRPC server  — OTLP gRPC on port 4317 (runs in ThreadPoolExecutor)
  2. HTTP server  — OTLP HTTP on port 4318 (aiohttp, fully async)

Both publish normalised telemetry to NATS JetStream.
Graceful shutdown on SIGTERM / SIGINT: drain NATS, stop gRPC, stop HTTP.
"""
import asyncio
import logging
import signal

from aiohttp import web

from .config import cfg
from .grpc_handler import create_grpc_server
from .http_handler import build_app
from .publisher import NATSPublisher

logging.basicConfig(
    level=getattr(logging, cfg.log_level, logging.INFO),
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)


async def serve() -> None:
    # ── NATS publisher ────────────────────────────────────────────────────────
    publisher = NATSPublisher(
        url=cfg.nats_url,
        user=cfg.nats_user,
        password=cfg.nats_password,
        prefix=cfg.metrics_prefix,
        stream_name=cfg.stream_name,
        stream_max_age_seconds=cfg.stream_max_age_seconds,
    )
    await publisher.connect()

    # ── gRPC server (runs in background thread pool) ──────────────────────────
    grpc_server = create_grpc_server(publisher, cfg.host, cfg.grpc_port)

    # ── aiohttp HTTP server ───────────────────────────────────────────────────
    http_app = build_app(publisher)
    http_runner = web.AppRunner(http_app)
    await http_runner.setup()
    http_site = web.TCPSite(http_runner, cfg.host, cfg.http_port)
    await http_site.start()

    logger.info(
        "OTLP gRPC listening on %s:%d, HTTP on %s:%d",
        cfg.host, cfg.grpc_port,
        cfg.host, cfg.http_port,
    )

    # ── Graceful shutdown ─────────────────────────────────────────────────────
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop_event.set)

    await stop_event.wait()

    logger.info("shutdown signal received")

    # Stop gRPC — grace period of 5 s for in-flight RPCs
    grpc_server.stop(grace=5)

    # Stop HTTP
    await http_runner.cleanup()

    # Drain NATS last so any in-flight publishes complete
    await publisher.drain()

    logger.info("ingest-otlp stopped")


def main() -> None:
    asyncio.run(serve())


if __name__ == "__main__":
    main()
