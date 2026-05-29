"""
gRPC servicer implementing gNMIDialOut.Publish.

Devices connect to this server and stream PublishResponse messages.
Each Notification inside a PublishResponse is parsed and forwarded
to NATS JetStream via the shared NATSPublisher.
"""
import logging

from google.protobuf import empty_pb2

from .parser import notification_to_dict
from .publisher import NATSPublisher

logger = logging.getLogger(__name__)


class GNMIDialOutServicer:
    """
    Async implementation of gNMIDialOut.Publish (client-streaming RPC).

    The generated base class is imported lazily inside the method
    so that this module can be imported before proto files are compiled
    (e.g. in tests that mock the proto objects).
    """

    def __init__(self, publisher: NATSPublisher) -> None:
        self._publisher = publisher

    async def Publish(self, request_iterator, context):
        """
        Accept a stream of PublishResponse messages from one device.
        Returns google.protobuf.Empty when the device closes the stream.
        """
        peer = context.peer()
        logger.info("dial-out stream opened from %s", peer)
        n_published = 0

        try:
            async for pub_resp in request_iterator:
                resp_kind = pub_resp.WhichOneof("response")

                if resp_kind == "update":
                    sub_resp = pub_resp.update
                    inner_kind = sub_resp.WhichOneof("response")

                    if inner_kind == "update":
                        notification = sub_resp.update
                        target = self._extract_target(notification, peer)
                        nd = notification_to_dict(notification, target)
                        await self._publisher.publish_notification(nd)
                        n_published += 1

                    elif inner_kind == "sync_response":
                        logger.debug("sync_response from %s", peer)

                elif resp_kind == "error":
                    logger.warning(
                        "error from device %s — code=%s msg=%s",
                        peer,
                        pub_resp.error.code,
                        pub_resp.error.message,
                    )

        except Exception as exc:  # covers grpc.aio.AbortError on abrupt disconnect
            logger.error("stream error from %s: %s", peer, exc, exc_info=True)

        finally:
            logger.info(
                "dial-out stream from %s closed — %d notifications published",
                peer, n_published,
            )

        return empty_pb2.Empty()

    # ── helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _extract_target(notification, peer: str) -> str:
        """
        Return the target identifier for this notification.

        Preference order:
          1. notification.prefix.target  (gNMI standard field)
          2. Peer address stripped of port and IPv6 brackets
        """
        if notification.prefix.target:
            return notification.prefix.target
        # Strip gRPC peer format:  "ipv4:1.2.3.4:PORT" or "ipv6:[::1]:PORT"
        addr = peer
        for prefix in ("ipv4:", "ipv6:"):
            if addr.startswith(prefix):
                addr = addr[len(prefix):]
                break
        addr = addr.split(":")[0].strip("[]")
        return addr or peer
