"""
Async UDP servers for NetFlow (v5/v9/IPFIX) and sFlow.

Each server is an asyncio DatagramProtocol.  Decoded FlowRecord instances are
handed to the correlator, then published via FlowPublisher.
"""
from __future__ import annotations

import asyncio
import logging
import time

from .correlator import FlowCorrelator
from .netflow_decoder import NetFlowDecoder
from .publisher import FlowPublisher
from .sflow_decoder import decode as sflow_decode

logger = logging.getLogger(__name__)

# One NetFlowDecoder per (exporter_ip, source_id).  The source_id is embedded
# in v9/IPFIX headers but not in v5, so we use "0" for v5.
_netflow_decoders: dict[str, NetFlowDecoder] = {}


def _get_nf_decoder(exporter_ip: str) -> NetFlowDecoder:
    if exporter_ip not in _netflow_decoders:
        _netflow_decoders[exporter_ip] = NetFlowDecoder(exporter_ip)
    return _netflow_decoders[exporter_ip]


class NetFlowProtocol(asyncio.DatagramProtocol):
    def __init__(self, publisher: FlowPublisher, correlator: FlowCorrelator) -> None:
        self._publisher   = publisher
        self._correlator  = correlator
        self._loop: asyncio.AbstractEventLoop | None = None

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self._loop = asyncio.get_event_loop()
        logger.info("NetFlow UDP server ready")

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        exporter_ip = addr[0]
        decoder = _get_nf_decoder(exporter_ip)
        try:
            records = decoder.decode(data)
        except Exception as exc:
            logger.debug("NetFlow decode error from %s: %s", exporter_ip, exc)
            return

        for record in records:
            asyncio.ensure_future(self._handle(record))

    async def _handle(self, record) -> None:
        observations = self._correlator.feed(record)
        await self._publisher.publish_flow(record)
        for obs in observations:
            await self._publisher.publish_latency(obs)

    def error_received(self, exc: Exception) -> None:
        logger.error("NetFlow UDP error: %s", exc)


class SFlowProtocol(asyncio.DatagramProtocol):
    def __init__(self, publisher: FlowPublisher, correlator: FlowCorrelator) -> None:
        self._publisher   = publisher
        self._correlator  = correlator

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        logger.info("sFlow UDP server ready")

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        exporter_ip = addr[0]
        recv_time   = time.time()
        try:
            records = sflow_decode(data, exporter_ip, recv_time)
        except Exception as exc:
            logger.debug("sFlow decode error from %s: %s", exporter_ip, exc)
            return

        for record in records:
            asyncio.ensure_future(self._handle(record))

    async def _handle(self, record) -> None:
        observations = self._correlator.feed(record)
        await self._publisher.publish_flow(record)
        for obs in observations:
            await self._publisher.publish_latency(obs)

    def error_received(self, exc: Exception) -> None:
        logger.error("sFlow UDP error: %s", exc)


async def start_servers(
    host: str,
    netflow_port: int,
    sflow_port: int,
    publisher: FlowPublisher,
    correlator: FlowCorrelator,
) -> tuple[asyncio.BaseTransport, asyncio.BaseTransport]:
    loop = asyncio.get_running_loop()

    nf_transport, _ = await loop.create_datagram_endpoint(
        lambda: NetFlowProtocol(publisher, correlator),
        local_addr=(host, netflow_port),
    )
    logger.info("NetFlow listening on %s:%d/udp", host, netflow_port)

    sf_transport, _ = await loop.create_datagram_endpoint(
        lambda: SFlowProtocol(publisher, correlator),
        local_addr=(host, sflow_port),
    )
    logger.info("sFlow listening on %s:%d/udp", host, sflow_port)

    return nf_transport, sf_transport


if __name__ == "__main__":
    # Runnable entrypoint (python -m ingest.flow_server). The full server
    # lifecycle lives in main.serve(); import lazily to avoid a circular import
    # (main imports start_servers from this module).
    from .main import main

    main()
