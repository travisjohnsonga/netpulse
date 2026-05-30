"""
Local device registry, kept in sync from NATS netpulse.devices.upsert/remove.

Lets the MDT/gNMI servicers map an incoming stream (by source IP, or by the
node-id/hostname inside the telemetry) to a NetPulse device_id, so published
metrics land under netpulse.telemetry.<device_id>.metrics.
"""
from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)


class DeviceRegistry:
    def __init__(self) -> None:
        self._by_ip: dict[str, str] = {}
        self._by_host: dict[str, str] = {}

    async def start(self, nc) -> None:
        await nc.subscribe("netpulse.devices.upsert", cb=self._on_upsert)
        await nc.subscribe("netpulse.devices.remove", cb=self._on_remove)
        logger.info("device registry subscribed to netpulse.devices.upsert/remove")

    async def _on_upsert(self, msg) -> None:
        try:
            d = json.loads(msg.data)
        except Exception:
            return
        device_id = d.get("device_id")
        if device_id is None:
            return
        device_id = str(device_id)
        ip = d.get("ip")
        host = d.get("hostname")
        if ip:
            self._by_ip[str(ip)] = device_id
        if host:
            self._by_host[str(host)] = device_id

    async def _on_remove(self, msg) -> None:
        try:
            device_id = str(json.loads(msg.data).get("device_id"))
        except Exception:
            return
        self._by_ip = {k: v for k, v in self._by_ip.items() if v != device_id}
        self._by_host = {k: v for k, v in self._by_host.items() if v != device_id}

    def resolve(self, ip: str | None = None, hostname: str | None = None) -> str | None:
        if hostname and hostname in self._by_host:
            return self._by_host[hostname]
        if ip and ip in self._by_ip:
            return self._by_ip[ip]
        return None
