"""
aiohttp webhook receiver for vendor-push events.

Routes:
    POST /webhooks/meraki  → MerakiPlugin.parse_webhook()
    POST /webhooks/mist    → MistPlugin.parse_webhook()
    POST /webhooks/unifi   → 404 (UniFi does not support outbound webhooks)

Security:
    - Content-Length enforced: reject > 1 MB
    - Meraki: shared-secret verified inside MerakiPlugin.parse_webhook()
    - Mist:   HMAC-SHA256 signature verified (if webhook_secret configured)
    - Source IP logged for every webhook delivery
"""
import hashlib
import hmac
import json
import logging
from typing import Callable

from aiohttp import web
from multidict import CIMultiDictProxy

from .models import VendorAlert, VendorDevice
from .publisher import NATSPublisher

logger = logging.getLogger(__name__)

MAX_BODY_BYTES = 1 * 1024 * 1024  # 1 MB


def _verify_mist_signature(body: bytes, headers: CIMultiDictProxy, secret: str) -> bool:
    """
    Verify X-Mist-Signature-v2 HMAC-SHA256.
    Returns True if secret is not configured (skip verification).
    """
    if not secret:
        return True
    signature = headers.get("X-Mist-Signature-v2") or headers.get("X-Mist-Signature", "")
    if not signature:
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def _source_ip(request: web.Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.remote or "unknown"


async def _read_body(request: web.Request) -> bytes | None:
    """Read request body, enforcing 1 MB cap. Returns None if too large."""
    content_length = request.content_length
    if content_length is not None and content_length > MAX_BODY_BYTES:
        return None
    body = await request.read()
    if len(body) > MAX_BODY_BYTES:
        return None
    return body


def build_app(
    *,
    publisher: NATSPublisher,
    get_meraki_plugin: Callable | None = None,
    get_mist_plugin: Callable | None = None,
    mist_webhook_secret: str = "",
) -> web.Application:
    """
    Build and return the aiohttp Application.

    `get_meraki_plugin` / `get_mist_plugin` are callables that return the
    first matching plugin instance (or None).  The scheduler passes these in
    so webhook events are routed to the correct plugin.
    """
    app = web.Application()

    async def handle_meraki(request: web.Request) -> web.Response:
        source_ip = _source_ip(request)
        body = await _read_body(request)
        if body is None:
            logger.warning("Meraki webhook from %s rejected: body too large", source_ip)
            return web.Response(status=413, text="Payload too large")

        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            logger.warning("Meraki webhook from %s: invalid JSON", source_ip)
            return web.Response(status=400, text="Invalid JSON")

        logger.info("Meraki webhook from %s", source_ip)

        if get_meraki_plugin is None:
            return web.Response(status=200, text="no meraki integration configured")

        plugin = get_meraki_plugin(payload.get("organizationId", ""))
        if plugin is None:
            logger.warning(
                "Meraki webhook from %s: no matching integration for org %s",
                source_ip, payload.get("organizationId"),
            )
            return web.Response(status=200, text="ok")

        events = plugin.parse_webhook(payload, source_ip)
        for event in events:
            if isinstance(event, VendorAlert):
                await publisher.publish_alert(event)
            elif isinstance(event, VendorDevice):
                await publisher.publish_device(event)

        return web.Response(status=200, text="ok")

    async def handle_mist(request: web.Request) -> web.Response:
        source_ip = _source_ip(request)
        body = await _read_body(request)
        if body is None:
            logger.warning("Mist webhook from %s rejected: body too large", source_ip)
            return web.Response(status=413, text="Payload too large")

        # HMAC verification
        if mist_webhook_secret:
            if not _verify_mist_signature(body, request.headers, mist_webhook_secret):
                logger.warning("Mist webhook from %s: invalid signature", source_ip)
                return web.Response(status=403, text="Invalid signature")

        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            logger.warning("Mist webhook from %s: invalid JSON", source_ip)
            return web.Response(status=400, text="Invalid JSON")

        logger.info("Mist webhook from %s: topic=%s", source_ip, payload.get("topic"))

        if get_mist_plugin is None:
            return web.Response(status=200, text="no mist integration configured")

        # Mist webhooks don't carry org_id at top level in all formats
        plugin = get_mist_plugin(payload.get("org_id", ""))
        if plugin is None:
            return web.Response(status=200, text="ok")

        events = plugin.parse_webhook(payload, source_ip)
        for event in events:
            if isinstance(event, VendorAlert):
                await publisher.publish_alert(event)
            elif isinstance(event, VendorDevice):
                await publisher.publish_device(event)

        return web.Response(status=200, text="ok")

    async def handle_unifi(request: web.Request) -> web.Response:
        return web.Response(status=404, text="UniFi does not support outbound webhooks")

    app.router.add_post("/webhooks/meraki", handle_meraki)
    app.router.add_post("/webhooks/mist", handle_mist)
    app.router.add_post("/webhooks/unifi", handle_unifi)

    return app
