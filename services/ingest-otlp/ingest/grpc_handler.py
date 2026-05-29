"""
Raw gRPC server for OTLP — no generated proto stubs required.

Uses grpcio's GenericMethodHandler / GenericRpcHandler APIs to accept
connections on port 4317, route each RPC by its full method path, read the
raw protobuf request bytes, normalise them, and publish to NATS.

Handled methods:
  /opentelemetry.proto.collector.metrics.v1.MetricsService/Export
  /opentelemetry.proto.collector.logs.v1.LogsService/Export
  /opentelemetry.proto.collector.trace.v1.TraceService/Export
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING

import grpc

from .normalizer import parse_logs, parse_metrics, parse_traces

if TYPE_CHECKING:
    from .publisher import NATSPublisher

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Empty success responses (minimal valid protobuf — all fields optional/empty)
# ---------------------------------------------------------------------------

# ExportMetricsServiceResponse / ExportLogsServiceResponse /
# ExportTraceServiceResponse are all empty messages (no required fields).
# A zero-length bytes object is a valid serialisation.
_EMPTY_RESPONSE = b""

# ---------------------------------------------------------------------------
# Method handler factory
# ---------------------------------------------------------------------------

_METRICS_METHOD = "/opentelemetry.proto.collector.metrics.v1.MetricsService/Export"
_LOGS_METHOD = "/opentelemetry.proto.collector.logs.v1.LogsService/Export"
_TRACES_METHOD = "/opentelemetry.proto.collector.trace.v1.TraceService/Export"


def _make_handler(signal_type: str, publisher: "NATSPublisher"):
    """Return a grpc.RpcMethodHandler for one OTLP signal type."""
    import asyncio

    def handle(request: bytes, context: grpc.ServicerContext) -> bytes:
        # Determine caller IP for subject routing
        peer = context.peer()  # e.g. "ipv4:1.2.3.4:12345"
        exporter_ip = _peer_to_ip(peer)

        loop = asyncio.get_event_loop()

        try:
            if signal_type == "metrics":
                items = parse_metrics(request, exporter_ip)
                for item in items:
                    asyncio.run_coroutine_threadsafe(
                        publisher.publish_metrics(exporter_ip, item.to_dict()),
                        loop,
                    ).result(timeout=5)
            elif signal_type == "logs":
                items = parse_logs(request, exporter_ip)
                for item in items:
                    asyncio.run_coroutine_threadsafe(
                        publisher.publish_logs(exporter_ip, item.to_dict()),
                        loop,
                    ).result(timeout=5)
            elif signal_type == "traces":
                items = parse_traces(request, exporter_ip)
                for item in items:
                    asyncio.run_coroutine_threadsafe(
                        publisher.publish_traces(exporter_ip, item.to_dict()),
                        loop,
                    ).result(timeout=5)
        except ImportError:
            # opentelemetry-proto stubs unavailable — log and return success
            # so the exporter does not back-pressure; data will come via HTTP/JSON
            logger.warning(
                "opentelemetry-proto not available; gRPC %s payload dropped (use HTTP/JSON path)",
                signal_type,
            )
        except Exception as exc:
            logger.error("error processing gRPC %s from %s: %s", signal_type, exporter_ip, exc)
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(exc))

        return _EMPTY_RESPONSE

    return grpc.unary_unary_rpc_method_handler(
        handle,
        request_deserializer=lambda b: b,   # pass raw bytes through
        response_serializer=lambda b: b,    # pass raw bytes through
    )


def _peer_to_ip(peer: str) -> str:
    """Extract an IP address string from a gRPC peer string like 'ipv4:1.2.3.4:56789'."""
    try:
        parts = peer.split(":")
        if parts[0] in ("ipv4", "ipv6"):
            return parts[1]
        return peer
    except Exception:
        return peer


# ---------------------------------------------------------------------------
# Generic RPC handler that dispatches by method name
# ---------------------------------------------------------------------------

class _OTLPGenericHandler(grpc.GenericRpcHandler):
    def __init__(self, publisher: "NATSPublisher") -> None:
        self._handlers = {
            _METRICS_METHOD: _make_handler("metrics", publisher),
            _LOGS_METHOD: _make_handler("logs", publisher),
            _TRACES_METHOD: _make_handler("traces", publisher),
        }

    def service_name(self):
        return "OTLPIngest"

    def service(self, handler_call_details: grpc.HandlerCallDetails):
        return self._handlers.get(handler_call_details.method)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def create_grpc_server(publisher: "NATSPublisher", host: str, port: int) -> grpc.Server:
    """
    Create and start a gRPC server that receives OTLP data.

    The server runs in a ThreadPoolExecutor so it does not block the asyncio
    event loop.  Callers should await asyncio.to_thread(server.wait_for_termination)
    or simply call server.stop() during shutdown.
    """
    server = grpc.server(
        ThreadPoolExecutor(max_workers=10),
        options=[
            ("grpc.max_receive_message_length", 4 * 1024 * 1024),  # 4 MB
        ],
    )
    server.add_generic_rpc_handlers([_OTLPGenericHandler(publisher)])
    address = f"{host}:{port}"
    server.add_insecure_port(address)
    server.start()
    logger.info("OTLP gRPC server started on %s", address)
    return server
