import json
import logging

from channels.generic.websocket import AsyncWebsocketConsumer

logger = logging.getLogger(__name__)


class TelemetryConsumer(AsyncWebsocketConsumer):
    """
    WebSocket endpoint: /ws/telemetry/

    Clients connect here to receive live telemetry events.
    The stream-processor publishes to the 'telemetry' channel group;
    this consumer fans updates out to all connected browsers.
    """

    GROUP = "telemetry"

    async def connect(self):
        from apps.core.ws_auth import ws_subprotocol
        user = self.scope.get("user")
        if user is None or not user.is_authenticated:
            await self.close(code=4401)
            return
        await self.channel_layer.group_add(self.GROUP, self.channel_name)
        await self.accept(ws_subprotocol(self.scope))
        logger.debug("WS telemetry connect: %s", self.channel_name)

    async def disconnect(self, code):
        await self.channel_layer.group_discard(self.GROUP, self.channel_name)
        logger.debug("WS telemetry disconnect: %s (code=%s)", self.channel_name, code)

    async def receive(self, text_data=None, bytes_data=None):
        # Clients are receive-only for now; ignore any inbound messages.
        pass

    # ── Channel layer event handlers ─────────────────────────────────────────

    async def telemetry_metric(self, event: dict):
        """Pushed by stream-processor: {'type': 'telemetry.metric', 'payload': {...}}"""
        await self.send(text_data=json.dumps(event.get("payload", event)))

    async def topology_update(self, event: dict):
        """Pushed when link utilization changes: topology_update with utilization map."""
        await self.send(text_data=json.dumps({"type": "topology_update", **event.get("payload", {})}))
