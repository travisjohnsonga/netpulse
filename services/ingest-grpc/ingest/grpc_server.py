"""
Entry point: python -m ingest.grpc_server

Starts an async gRPC server that implements gNMIDialOut.Publish.
Devices establish outbound TCP connections here and stream telemetry;
each Notification is parsed and published to NATS JetStream.
"""
import asyncio
import logging
import signal

import grpc

# Importing the ingest package (via __init__.py) adds proto_generated/ to
# sys.path, so the generated modules are importable below.
import gnmi_pb2_grpc  # noqa: E402 — available after proto compilation
import mdt_grpc_dialout_pb2_grpc  # noqa: E402 — Cisco MDT dial-out

from .config import cfg
from .device_registry import DeviceRegistry
from .gnmi_heartbeat import GNMIHeartbeat
from .mdt_servicer import CiscoMDTServicer
from .publisher import NATSPublisher
from .servicer import GNMIDialOutServicer

logging.basicConfig(
    level=getattr(logging, cfg.log_level, logging.INFO),
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)


class _MethodLogInterceptor(grpc.aio.ServerInterceptor):
    """Log the gRPC method of every incoming RPC (diagnostics for dial-out)."""

    async def intercept_service(self, continuation, handler_call_details):
        logger.debug("gRPC call: method=%s", handler_call_details.method)
        return await continuation(handler_call_details)


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

    # Device registry (source IP / node-id → device_id) kept in sync from NATS.
    registry = DeviceRegistry()
    await registry.start(publisher.nc)

    # gNMI liveness heartbeat → Valkey (drives ingest-snmp adaptive polling).
    heartbeat = GNMIHeartbeat(url=cfg.valkey_url, ttl=cfg.gnmi_heartbeat_ttl)

    server = grpc.aio.server(interceptors=[_MethodLogInterceptor()])
    # OpenConfig gNMI dial-out (standard).
    gnmi_pb2_grpc.add_gNMIDialOutServicer_to_server(
        GNMIDialOutServicer(publisher), server
    )
    # Cisco IOS-XE/XR Model-Driven Telemetry dial-out (encode-kvgpb over grpc-tcp).
    mdt_grpc_dialout_pb2_grpc.add_gRPCMdtDialoutServicer_to_server(
        CiscoMDTServicer(publisher, registry, heartbeat), server
    )

    listen_addr = f"{cfg.grpc_host}:{cfg.grpc_port}"
    server.add_insecure_port(listen_addr)
    await server.start()
    logger.info(
        "gRPC/gNMI dial-out server listening on %s (insecure) — "
        "NATS stream=%s prefix=%s",
        listen_addr, cfg.stream_name, cfg.subject_prefix,
    )

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop_event.set)

    await stop_event.wait()

    logger.info("shutdown signal received — draining...")
    await server.stop(grace=10)
    await heartbeat.close()
    await publisher.drain()
    logger.info("ingest-grpc stopped")


def main() -> None:
    asyncio.run(serve())


if __name__ == "__main__":
    main()
