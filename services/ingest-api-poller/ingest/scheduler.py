"""
Polling scheduler for vendor API integrations.

Each integration runs in its own asyncio task, polling on its configured
interval.  A stop event coordinates graceful shutdown.
"""
import asyncio
import logging

from .base_plugin import VendorAPIPlugin
from .credentials import CredentialError, CredentialManager
from .plugins import PLUGIN_REGISTRY
from .publisher import NATSPublisher

logger = logging.getLogger(__name__)


class PollingScheduler:
    """Runs each plugin on its configured interval using asyncio tasks."""

    def __init__(self, publisher: NATSPublisher, credential_manager: CredentialManager) -> None:
        self._publisher = publisher
        self._cred_manager = credential_manager
        self._integrations: list[dict] = []
        self._tasks: list[asyncio.Task] = []
        self._stopped = False

    def register(self, integration_config: dict) -> None:
        """
        Queue an integration for scheduling.

        The plugin will be instantiated at run-time once credentials are
        available, so registration itself is synchronous and cheap.
        """
        if not integration_config.get("enabled", True):
            logger.info(
                "integration %r is disabled — skipping",
                integration_config.get("id"),
            )
            return
        vendor = integration_config.get("vendor", "")
        if vendor not in PLUGIN_REGISTRY:
            logger.warning(
                "unknown vendor %r for integration %r — skipping",
                vendor,
                integration_config.get("id"),
            )
            return
        self._integrations.append(integration_config)
        logger.info(
            "registered integration %r (vendor=%s interval=%ss)",
            integration_config.get("id"),
            vendor,
            integration_config.get("poll_interval", 60),
        )

    async def run(self) -> None:
        """Start polling tasks for all registered integrations concurrently."""
        self._stopped = False
        self._tasks = [
            asyncio.create_task(
                self._init_and_poll(cfg),
                name=f"poll-{cfg.get('id', 'unknown')}",
            )
            for cfg in self._integrations
        ]
        if not self._tasks:
            logger.warning("no integrations registered — scheduler idle")
            # Still return so the outer server can wait on stop
            return
        await asyncio.gather(*self._tasks, return_exceptions=True)

    def stop(self) -> None:
        """Signal all polling loops to exit after the current iteration."""
        self._stopped = True
        for task in self._tasks:
            task.cancel()

    async def _init_and_poll(self, integration_config: dict) -> None:
        """Fetch credentials and then enter the polling loop."""
        integration_id = integration_config.get("id", "unknown")
        cred_path = integration_config.get("cred_path", "")
        vendor = integration_config.get("vendor", "")
        plugin_class = PLUGIN_REGISTRY[vendor]

        # Credential fetch with retry so a temporary OpenBao hiccup at startup
        # does not permanently kill this integration's polling.
        credentials: dict = {}
        while not self._stopped:
            try:
                credentials = await self._cred_manager.get(cred_path)
                break
            except CredentialError as exc:
                logger.error(
                    "could not fetch credentials for %r: %s — retrying in 30s",
                    integration_id, exc,
                )
                await asyncio.sleep(30)

        if self._stopped:
            return

        plugin: VendorAPIPlugin = plugin_class(
            integration_id=integration_id,
            config=integration_config,
            credentials=credentials,
        )
        await self._poll_loop(plugin)

    async def _poll_loop(self, plugin: VendorAPIPlugin) -> None:
        """Poll forever: devices then alerts (then metrics), sleep interval, repeat."""
        integration_id = plugin.integration_id
        logger.info(
            "starting poll loop for %r (interval=%ds)",
            integration_id, plugin.poll_interval,
        )
        while not self._stopped:
            try:
                devices = await plugin.fetch_devices()
                for d in devices:
                    await self._publisher.publish_device(d)
                logger.debug(
                    "published %d device(s) for %r", len(devices), integration_id
                )

                alerts = await plugin.fetch_alerts()
                for a in alerts:
                    await self._publisher.publish_alert(a)
                logger.debug(
                    "published %d alert(s) for %r", len(alerts), integration_id
                )

                metrics = await plugin.fetch_metrics()
                for m in metrics:
                    await self._publisher.publish_metric(m)
                if metrics:
                    logger.debug(
                        "published %d metric(s) for %r", len(metrics), integration_id
                    )

            except asyncio.CancelledError:
                logger.info("poll loop cancelled for %r", integration_id)
                return
            except Exception as exc:
                logger.error("poll error for %r: %s", integration_id, exc)

            try:
                await asyncio.sleep(plugin.poll_interval)
            except asyncio.CancelledError:
                logger.info("poll loop cancelled during sleep for %r", integration_id)
                return
