"""Write per-collector config bundles to a JetStream KV bucket (config-DOWN).

Each remote collector has its own KV bucket ``collector-config-<id>`` on the
central NATS/JetStream; the collector-agent watches it and applies changes. This
is best-effort and gated by settings.COLLECTOR_CONFIG_PUBLISH (off in tests):
a NATS hiccup must never break the API request that triggered the change.

Mirrors apps.devices.snmp_publish's connection style. KV writes go to the hub
JetStream; the leaf transport (added later) is what carries the watch to remote
collectors.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os

from django.conf import settings

from .collector_config import build_config

logger = logging.getLogger(__name__)


def bucket_name(collector_id) -> str:
    return f"collector-config-{collector_id}"


def _enabled() -> bool:
    return bool(getattr(settings, "COLLECTOR_CONFIG_PUBLISH", True))


async def _connect():
    import nats  # lazy

    return await nats.connect(
        os.environ.get("NATS_URL", getattr(settings, "NATS_URL", "nats://nats:4222")),
        user=os.environ.get("NATS_USER", getattr(settings, "NATS_USER", "")) or None,
        password=os.environ.get("NATS_PASSWORD", getattr(settings, "NATS_PASSWORD", "")) or None,
        connect_timeout=3,
    )


async def _put(collector_id, config: dict) -> None:
    nc = await _connect()
    try:
        js = nc.jetstream()
        name = bucket_name(collector_id)
        try:
            kv = await js.key_value(name)
        except Exception:  # bucket doesn't exist yet — create it
            from nats.js.api import KeyValueConfig
            kv = await js.create_key_value(config=KeyValueConfig(bucket=name, history=5))
        await kv.put("config", json.dumps(config).encode())
    finally:
        await nc.drain()


def _run(collector_id, config: dict) -> bool:
    try:
        asyncio.run(_put(collector_id, config))
        return True
    except Exception as exc:  # NATS down, etc. — never break the request.
        logger.warning("collector config publish failed for %s: %s", collector_id, exc)
        return False


def publish_collector_config(collector) -> bool:
    """Build + write one collector's config bundle (best-effort)."""
    if not _enabled():
        return False
    return _run(collector.id, build_config(collector))


def publish_all_collectors() -> int:
    """Publish bundles for every remote collector. Returns the count published."""
    if not _enabled():
        return 0
    from .models import Collector

    n = 0
    for c in Collector.objects.filter(collector_type=Collector.CollectorType.REMOTE):
        if _run(c.id, build_config(c)):
            n += 1
    return n
