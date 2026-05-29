"""
Entry point: python -m ingest.poller_server

Starts two concurrent subsystems:
  1. PollingScheduler  — per-integration asyncio tasks polling vendor APIs
  2. Webhook receiver  — aiohttp HTTP server receiving vendor-push events

Integration configuration arrives via INTEGRATIONS_JSON env var (JSON array).
Credentials are fetched from OpenBao at startup and cached with TTL.
"""
import asyncio
import logging
import signal

from aiohttp import web

from .config import cfg
from .credentials import CredentialManager
from .plugins import PLUGIN_REGISTRY, MerakiPlugin, MistPlugin
from .publisher import NATSPublisher
from .scheduler import PollingScheduler
from .webhook_receiver import build_app

logging.basicConfig(
    level=getattr(logging, cfg.log_level, logging.INFO),
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)


async def serve() -> None:
    # ── NATS publisher ────────────────────────────────────────────────────────
    publisher = NATSPublisher(
        url=cfg.nats_url,
        user=cfg.nats_user,
        password=cfg.nats_password,
        prefix=cfg.vendor_prefix,
        stream_name=cfg.stream_name,
        stream_max_age_seconds=cfg.stream_max_age_seconds,
    )
    await publisher.connect()

    # ── OpenBao credential manager ────────────────────────────────────────────
    cred_manager = CredentialManager(
        addr=cfg.openbao_addr,
        token=cfg.openbao_token,
        cache_ttl=cfg.cred_cache_ttl,
    )

    # ── Load integrations ─────────────────────────────────────────────────────
    integrations = cfg.load_integrations()
    if not integrations:
        logger.warning("INTEGRATIONS_JSON is empty — no vendor APIs will be polled")

    # ── Polling scheduler ─────────────────────────────────────────────────────
    scheduler = PollingScheduler(publisher=publisher, credential_manager=cred_manager)
    for integration in integrations:
        try:
            scheduler.register(integration)
        except Exception as exc:
            logger.warning("could not register integration %r: %s", integration.get("id"), exc)

    # ── Plugin lookup helpers for webhook routing ─────────────────────────────
    # Build a map of org_id → plugin instance for webhook dispatch.
    # Plugins are instantiated here with empty credentials (credentials are
    # fetched lazily per poll cycle inside scheduler); for webhooks we need
    # the plugin instances to call parse_webhook().  We create lightweight
    # "webhook-only" plugin instances sharing the same credential manager.

    _meraki_plugins: dict[str, MerakiPlugin] = {}
    _mist_plugins: dict[str, MistPlugin] = {}

    for integration in integrations:
        vendor = integration.get("vendor", "")
        if not integration.get("enabled", True):
            continue
        if vendor not in PLUGIN_REGISTRY:
            continue
        cred_path = integration.get("cred_path", "")
        try:
            creds = await cred_manager.get(cred_path)
        except Exception as exc:
            logger.warning(
                "skipping webhook setup for %r — could not fetch creds: %s",
                integration.get("id"), exc,
            )
            creds = {}
        if vendor == "meraki":
            plugin = MerakiPlugin(
                integration_id=integration.get("id", ""),
                config=integration,
                credentials=creds,
            )
            org_id = str(integration.get("org_id", ""))
            _meraki_plugins[org_id] = plugin
        elif vendor == "mist":
            plugin = MistPlugin(
                integration_id=integration.get("id", ""),
                config=integration,
                credentials=creds,
            )
            org_id = str(integration.get("org_id", ""))
            _mist_plugins[org_id] = plugin

    def get_meraki_plugin(org_id: str):
        return _meraki_plugins.get(org_id) or (
            next(iter(_meraki_plugins.values()), None) if _meraki_plugins else None
        )

    def get_mist_plugin(org_id: str):
        return _mist_plugins.get(org_id) or (
            next(iter(_mist_plugins.values()), None) if _mist_plugins else None
        )

    # ── Webhook receiver ──────────────────────────────────────────────────────
    app = build_app(
        publisher=publisher,
        get_meraki_plugin=get_meraki_plugin,
        get_mist_plugin=get_mist_plugin,
    )
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, cfg.host, cfg.webhook_port)
    await site.start()
    logger.info("webhook receiver listening on %s:%d", cfg.host, cfg.webhook_port)

    # ── Graceful shutdown ─────────────────────────────────────────────────────
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop_event.set)

    logger.info(
        "ingest-api-poller running — %d integration(s) configured",
        len(integrations),
    )

    # Run scheduler concurrently with the stop-event wait
    scheduler_task = asyncio.create_task(scheduler.run(), name="scheduler")

    await stop_event.wait()

    logger.info("shutdown signal received")
    scheduler.stop()
    await asyncio.wait_for(asyncio.shield(scheduler_task), timeout=10)
    await runner.cleanup()
    await publisher.drain()
    logger.info("ingest-api-poller stopped")


def main() -> None:
    asyncio.run(serve())


if __name__ == "__main__":
    main()
