"""
Entry point: python -m ingest.flow_server  (or  python -m ingest.main)

Starts two concurrent UDP servers:
  • NetFlow/IPFIX on UDP 2055
  • sFlow on UDP 6343

Both feed decoded FlowRecords into the correlator and publish via NATS.
Latency observations are also written to InfluxDB.
"""
from __future__ import annotations

import asyncio
import logging
import signal

from .config import cfg
from .correlator import FlowCorrelator
from .flow_server import start_servers
from .publisher import FlowPublisher

logging.basicConfig(
    level=getattr(logging, cfg.log_level, logging.INFO),
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)


async def serve() -> None:
    publisher = FlowPublisher(
        nats_url=cfg.nats_url,
        nats_user=cfg.nats_user,
        nats_password=cfg.nats_password,
        prefix=cfg.flows_prefix,
        stream_name=cfg.stream_name,
        stream_max_age_seconds=cfg.stream_max_age_seconds,
        influxdb_url=cfg.influxdb_url,
        influxdb_token=cfg.influxdb_token,
        influxdb_org=cfg.influxdb_org,
        influxdb_bucket=cfg.influxdb_bucket,
    )
    await publisher.connect()

    correlator = FlowCorrelator(
        window=cfg.correlation_window,
        max_per_key=cfg.correlation_max_per_key,
    )

    try:
        nf_transport, sf_transport = await start_servers(
            host=cfg.host,
            netflow_port=cfg.netflow_port,
            sflow_port=cfg.sflow_port,
            publisher=publisher,
            correlator=correlator,
        )
    except PermissionError:
        logger.error(
            "Cannot bind UDP ports %d or %d (need NET_BIND_SERVICE or ports > 1024). "
            "Set NETFLOW_PORT / SFLOW_PORT in .env for local dev.",
            cfg.netflow_port, cfg.sflow_port,
        )
        raise

    logger.info(
        "ingest-flow running — NetFlow UDP %s:%d, sFlow UDP %s:%d",
        cfg.host, cfg.netflow_port,
        cfg.host, cfg.sflow_port,
    )

    # Periodic correlator eviction
    async def evict_loop() -> None:
        while True:
            await asyncio.sleep(cfg.correlation_window)
            correlator.evict_all_stale()

    evict_task = asyncio.create_task(evict_loop())

    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop_event.set)

    await stop_event.wait()

    logger.info("shutdown signal received")
    evict_task.cancel()
    nf_transport.close()
    sf_transport.close()
    await publisher.drain()
    logger.info("ingest-flow stopped")


def main() -> None:
    asyncio.run(serve())


if __name__ == "__main__":
    main()
