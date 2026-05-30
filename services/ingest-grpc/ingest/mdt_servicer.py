"""
gRPC servicer for Cisco MDT dial-out (mdt_dialout.gRPCMdtDialout/MdtDialout).

IOS-XE/XR devices configured with `... protocol grpc-tcp` stream MdtDialoutArgs
whose `data` bytes are a serialized telemetry.Telemetry message. We decode it,
flatten the kvgpb fields, resolve the device, and publish to
netpulse.telemetry.<device_id>.metrics (protocol=gnmi).
"""
import datetime as _dt
import logging

from .device_registry import DeviceRegistry
from .mdt_parser import flatten_metrics, parse_telemetry
from .publisher import NATSPublisher

logger = logging.getLogger(__name__)


def _peer_ip(peer: str) -> str:
    """'ipv4:1.2.3.4:50123' / 'ipv6:[::1]:50123' → '1.2.3.4' / '::1'."""
    addr = peer
    for prefix in ("ipv4:", "ipv6:"):
        if addr.startswith(prefix):
            addr = addr[len(prefix):]
            break
    return addr.rsplit(":", 1)[0].strip("[]") or peer


def _iso(msg_timestamp_ms: int) -> str:
    # Cisco msg_timestamp is milliseconds since the Unix epoch.
    try:
        if msg_timestamp_ms:
            return _dt.datetime.fromtimestamp(msg_timestamp_ms / 1000, _dt.timezone.utc).isoformat()
    except (ValueError, OverflowError, OSError):
        pass
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


class CiscoMDTServicer:
    """Async implementation of gRPCMdtDialout.MdtDialout (bidirectional stream)."""

    def __init__(self, publisher: NATSPublisher, registry: DeviceRegistry) -> None:
        self._publisher = publisher
        self._registry = registry

    async def MdtDialout(self, request_iterator, context):
        peer = context.peer()
        src_ip = _peer_ip(peer)
        logger.info("MDT dial-out stream opened from %s", peer)
        n_published = 0
        try:
            import cisco_telemetry_pb2 as tpb  # available after proto compilation

            async for args in request_iterator:
                data = getattr(args, "data", b"")
                if not data:
                    continue
                telem = tpb.Telemetry()
                try:
                    telem.ParseFromString(data)
                except Exception as exc:
                    logger.warning("could not parse MDT telemetry from %s: %s", peer, exc)
                    continue

                parsed = parse_telemetry(telem)
                device_id = (
                    self._registry.resolve(ip=src_ip, hostname=parsed["node_id"])
                    or parsed["node_id"] or src_ip
                )
                metrics = flatten_metrics(parsed)
                payload = {
                    "device_id": str(device_id),
                    "protocol": "gnmi",
                    "node_id": parsed["node_id"],
                    "subscription": parsed["subscription"],
                    "encoding_path": parsed["encoding_path"],
                    "timestamp": _iso(parsed["msg_timestamp"]),
                    "metrics": metrics,
                    "rows": parsed["rows"],
                }
                await self._publisher.publish_metrics(str(device_id), payload)
                n_published += 1
                logger.debug(
                    "MDT msg from %s (dev=%s) path=%s rows=%d metrics=%d",
                    peer, device_id, parsed["encoding_path"], len(parsed["rows"]), len(metrics),
                )
        except Exception as exc:  # abrupt disconnect, etc.
            logger.error("MDT stream error from %s: %s", peer, exc, exc_info=True)
        finally:
            logger.info("MDT dial-out stream from %s closed — %d message(s) published", peer, n_published)
        # Bidirectional RPC: we send no responses. The trailing (unreachable)
        # yield marks this as an async generator so grpc.aio treats it as a
        # streaming-response handler.
        return
        yield  # noqa
