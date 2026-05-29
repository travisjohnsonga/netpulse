import json
import logging

from channels.generic.websocket import AsyncWebsocketConsumer

logger = logging.getLogger(__name__)


class AlertConsumer(AsyncWebsocketConsumer):
    """
    WebSocket endpoint: /ws/alerts/

    Clients connect here to receive real-time alert notifications.
    The alert-engine publishes to the 'alerts' channel group when
    a new alert fires or changes state.
    """

    GROUP = "alerts"

    async def connect(self):
        await self.channel_layer.group_add(self.GROUP, self.channel_name)
        await self.accept()
        logger.debug("WS alerts connect: %s", self.channel_name)

    async def disconnect(self, code):
        await self.channel_layer.group_discard(self.GROUP, self.channel_name)
        logger.debug("WS alerts disconnect: %s (code=%s)", self.channel_name, code)

    async def receive(self, text_data=None, bytes_data=None):
        pass

    # ── Channel layer event handlers ─────────────────────────────────────────

    async def alert_fired(self, event: dict):
        """Pushed by alert-engine when a new alert fires."""
        await self.send(text_data=json.dumps({"type": "alert_fired", **event.get("payload", {})}))

    async def alert_resolved(self, event: dict):
        """Pushed by alert-engine when an alert is resolved."""
        await self.send(text_data=json.dumps({"type": "alert_resolved", **event.get("payload", {})}))

    async def alert_acknowledged(self, event: dict):
        """Pushed when an alert is acknowledged via the API."""
        await self.send(text_data=json.dumps({"type": "alert_acknowledged", **event.get("payload", {})}))
