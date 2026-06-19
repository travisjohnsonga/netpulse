import json
import logging

from channels.generic.websocket import AsyncWebsocketConsumer

logger = logging.getLogger(__name__)


class DeviceStatusConsumer(AsyncWebsocketConsumer):
    """
    WebSocket endpoint: /ws/devices/

    Clients connect here to receive real-time device reachability/status
    changes. The reachability monitor pushes to the 'devices' channel group
    when a device transitions reachable ↔ unreachable.
    """

    GROUP = "devices"

    async def connect(self):
        from apps.core.ws_auth import ws_subprotocol
        user = self.scope.get("user")
        if user is None or not user.is_authenticated:
            await self.close(code=4401)
            return
        await self.channel_layer.group_add(self.GROUP, self.channel_name)
        await self.accept(ws_subprotocol(self.scope))

    async def disconnect(self, code):
        await self.channel_layer.group_discard(self.GROUP, self.channel_name)

    async def receive(self, text_data=None, bytes_data=None):
        pass

    async def device_status(self, event: dict):
        """Pushed when a device's reachability/status changes."""
        await self.send(text_data=json.dumps({"type": "device_status", **event.get("payload", {})}))

    async def topology_updated(self, event: dict):
        """Pushed when discovery/enrichment changes topology links."""
        await self.send(text_data=json.dumps({"type": "topology_updated", **event.get("payload", {})}))
