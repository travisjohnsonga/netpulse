"""Entry point: wire up writers and start the NATS consumer loop."""
from __future__ import annotations

import asyncio
import logging

from stream_processor import config
from stream_processor.consumer import run
from stream_processor.writers.influx import InfluxWriter
from stream_processor.writers.opensearch import OpenSearchWriter


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    influx = InfluxWriter(
        url=config.INFLUX_URL,
        token=config.INFLUX_TOKEN,
        org=config.INFLUX_ORG,
        bucket=config.INFLUX_BUCKET,
    )
    os_writer = OpenSearchWriter(
        url=config.OPENSEARCH_URL,
        user=config.OPENSEARCH_USER,
        password=config.OPENSEARCH_PASS,
        batch_size=config.BATCH_SIZE,
        batch_timeout=config.BATCH_TIMEOUT,
    )
    asyncio.run(run(influx=influx, os_writer=os_writer))


if __name__ == "__main__":
    main()
