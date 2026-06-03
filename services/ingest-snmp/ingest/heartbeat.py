"""Service liveness heartbeat — writes service:heartbeat:{name} to Valkey every
`interval`s with a `ttl` expiry, so run_health_checks can see the service is up."""
import asyncio
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


async def heartbeat_loop(valkey_url: str, service_name: str, stop_event: asyncio.Event,
                         interval: int = 60, ttl: int = 300) -> None:
    try:
        import redis.asyncio as redis
    except Exception as exc:  # redis not installed → heartbeat disabled
        logger.warning("heartbeat: redis unavailable (%s) — disabled", exc)
        return
    client = redis.from_url(valkey_url)
    key = f"service:heartbeat:{service_name}"
    try:
        while not stop_event.is_set():
            try:
                await client.set(key, datetime.now(timezone.utc).isoformat(), ex=ttl)
            except Exception as exc:  # a transient Valkey hiccup must not stop the service
                logger.debug("heartbeat write failed: %s", exc)
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=interval)
            except asyncio.TimeoutError:
                pass
    finally:
        try:
            await client.aclose()
        except Exception:
            pass
