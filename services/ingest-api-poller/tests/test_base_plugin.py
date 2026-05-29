"""
Tests for VendorAPIPlugin abstract base class defaults.
"""
import asyncio
import unittest

from ingest.base_plugin import VendorAPIPlugin
from ingest.models import VendorAlert, VendorDevice, VendorMetric


class ConcretePlugin(VendorAPIPlugin):
    """Minimal concrete subclass for testing base behaviour."""
    vendor = "test"

    async def fetch_devices(self) -> list[VendorDevice]:
        return []

    async def fetch_alerts(self) -> list[VendorAlert]:
        return []


class TestVendorAPIPluginDefaults(unittest.TestCase):
    def _make_plugin(self, config: dict | None = None, credentials: dict | None = None):
        return ConcretePlugin(
            integration_id="test-integration-1",
            config=config or {},
            credentials=credentials or {},
        )

    def test_parse_webhook_default_returns_empty_list(self):
        plugin = self._make_plugin()
        result = plugin.parse_webhook({"some": "payload"}, source_ip="1.2.3.4")
        self.assertEqual(result, [])

    def test_parse_webhook_default_returns_list_type(self):
        plugin = self._make_plugin()
        result = plugin.parse_webhook({}, source_ip="10.0.0.1")
        self.assertIsInstance(result, list)

    def test_poll_interval_uses_config_value(self):
        plugin = self._make_plugin(config={"poll_interval": 120})
        self.assertEqual(plugin.poll_interval, 120)

    def test_poll_interval_defaults_to_60(self):
        plugin = self._make_plugin(config={})
        self.assertEqual(plugin.poll_interval, 60)

    def test_poll_interval_coerces_string_to_int(self):
        plugin = self._make_plugin(config={"poll_interval": "90"})
        self.assertEqual(plugin.poll_interval, 90)

    def test_fetch_metrics_default_returns_empty_list(self):
        plugin = self._make_plugin()
        result = asyncio.run(plugin.fetch_metrics())
        self.assertEqual(result, [])

    def test_integration_id_stored(self):
        plugin = ConcretePlugin(
            integration_id="my-org",
            config={},
            credentials={},
        )
        self.assertEqual(plugin.integration_id, "my-org")

    def test_vendor_attribute(self):
        plugin = self._make_plugin()
        self.assertEqual(plugin.vendor, "test")


if __name__ == "__main__":
    unittest.main()
