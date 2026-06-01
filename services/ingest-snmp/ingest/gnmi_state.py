"""
Read the per-device gNMI liveness heartbeat (written by ingest-grpc) from
Valkey to drive adaptive polling.

A device is "gNMI active" when gnmi:last_seen:{device_id} exists and its
timestamp is newer than ``threshold`` seconds. While active, the SNMP poller
suppresses its (now-redundant) polling for that device — gNMI provides the
metrics. When the stream stalls the key ages out / expires and the device is no
longer active, so SNMP automatically resumes as the fallback.

Graceful degradation: if Valkey is unreachable or the key is malformed,
``is_active`` returns False (the safe default — poll via SNMP) and logs a
warning once, so a Valkey outage never silently stops monitoring.
"""
import datetime as _dt
import logging

logger = logging.getLogger(__name__)

KEY_TEMPLATE = "gnmi:last_seen:{}"


class GNMIActivity:
    def __init__(self, url: str, threshold_seconds: int = 120) -> None:
        self._url = url
        self._threshold = threshold_seconds
        self._client = None
        self._warned = False

    def _get_client(self):
        if self._client is None:
            import redis.asyncio as redis  # lazy import
            self._client = redis.from_url(
                self._url, socket_timeout=2, socket_connect_timeout=2,
            )
        return self._client

    async def is_active(self, device_id: str) -> bool:
        """True iff a fresh (< threshold) gNMI heartbeat exists for the device."""
        try:
            client = self._get_client()
            raw = await client.get(KEY_TEMPLATE.format(device_id))
            if self._warned:
                logger.warning("Valkey recovered — SNMP adaptive polling re-enabled")
                self._warned = False
        except Exception as exc:
            if not self._warned:
                logger.warning(
                    "Valkey unavailable — SNMP adaptive polling disabled, "
                    "polling all devices: %s", exc,
                )
                self._warned = True
            return False

        if not raw:
            return False

        try:
            last_seen = _dt.datetime.fromisoformat(
                raw.decode() if isinstance(raw, (bytes, bytearray)) else str(raw)
            )
            age = (_dt.datetime.now(_dt.timezone.utc) - last_seen).total_seconds()
        except (ValueError, TypeError) as exc:
            logger.warning("malformed gNMI heartbeat for %s (%r): %s", device_id, raw, exc)
            return False

        return age < self._threshold

    async def close(self) -> None:
        if self._client is not None:
            try:
                await self._client.aclose()
            except Exception:
                pass
