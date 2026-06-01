"""
Per-device gNMI liveness heartbeat, stored in Valkey.

Every time a device streams a gNMI/MDT message we stamp a key:

    gnmi:last_seen:{device_id}  ->  <UTC ISO-8601 timestamp>   (EX = TTL)

ingest-snmp reads this key to decide whether to suppress redundant SNMP
polling (a device that is actively streaming gNMI doesn't also need to be
SNMP-polled for the same metrics), and the API surfaces it in
/devices/{id}/collection-status/.

The key carries a TTL (default 180s = 3× the 30s sample interval) so it
auto-expires when streaming stops — SNMP then resumes automatically with no
explicit "stream lost" signal needed.

Best-effort by design: a Valkey outage must never interrupt telemetry ingest,
so every error is swallowed (logged once until it recovers).
"""
import datetime as _dt
import logging

logger = logging.getLogger(__name__)

KEY_TEMPLATE = "gnmi:last_seen:{}"


class GNMIHeartbeat:
    def __init__(self, url: str, ttl: int = 180) -> None:
        self._url = url
        self._ttl = ttl
        self._client = None
        self._warned = False

    def _get_client(self):
        if self._client is None:
            import redis.asyncio as redis  # lazy — keeps proto-only test imports light
            self._client = redis.from_url(
                self._url, socket_timeout=2, socket_connect_timeout=2,
            )
        return self._client

    async def mark_active(self, device_id: str) -> None:
        """Stamp gnmi:last_seen:{device_id} = now (UTC ISO) with the TTL."""
        try:
            client = self._get_client()
            now = _dt.datetime.now(_dt.timezone.utc).isoformat()
            await client.set(KEY_TEMPLATE.format(device_id), now, ex=self._ttl)
            if self._warned:
                logger.info("Valkey recovered — gNMI heartbeat resumed")
                self._warned = False
        except Exception as exc:
            if not self._warned:
                logger.warning(
                    "Valkey unavailable — gNMI heartbeat disabled "
                    "(SNMP adaptive polling will fall back to full polling): %s", exc,
                )
                self._warned = True

    async def close(self) -> None:
        if self._client is not None:
            try:
                await self._client.aclose()
            except Exception:
                pass
