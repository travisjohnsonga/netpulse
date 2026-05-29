"""
Tests for vendor plugin implementations.

Uses unittest.mock to avoid real network calls.
"""
import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from ingest.models import VendorAlert, VendorDevice, VendorMetric
from ingest.plugins.meraki import MerakiPlugin
from ingest.plugins.mist import MistPlugin
from ingest.plugins.unifi import UniFiPlugin


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _meraki_plugin(org_id: str = "111111", webhook_secret: str = "s3cr3t"):
    return MerakiPlugin(
        integration_id="meraki-org1",
        config={"org_id": org_id, "poll_interval": 60},
        credentials={"api_key": "REDACTED", "webhook_secret": webhook_secret},
    )


def _mist_plugin(org_id: str = "aaa-bbb-ccc"):
    return MistPlugin(
        integration_id="mist-org1",
        config={"org_id": org_id, "poll_interval": 60},
        credentials={"api_token": "REDACTED"},
    )


def _unifi_plugin():
    return UniFiPlugin(
        integration_id="unifi-site1",
        config={"site": "default", "poll_interval": 120},
        credentials={
            "username": "admin",
            "password": "REDACTED",
            "base_url": "https://unifi.local:8443",
            "verify_ssl": False,
        },
    )


# ──────────────────────────────────────────────────────────────────────────────
# VendorDevice model
# ──────────────────────────────────────────────────────────────────────────────

class TestVendorDeviceModel(unittest.TestCase):
    def _make_device(self):
        return VendorDevice(
            integration_id="meraki-org1",
            vendor="meraki",
            vendor_device_id="SERIAL123",
            name="AP-01",
            model="MR46",
            serial="SERIAL123",
            mac="00:11:22:33:44:55",
            status="online",
            ip_address="192.168.1.100",
            firmware="29.7.1",
            site_id="N_12345",
            site_name="HQ Network",
            tags=["floor-1"],
            raw={"extra": "data"},
        )

    def test_to_dict_has_required_keys(self):
        d = self._make_device().to_dict()
        required = [
            "integration_id", "vendor", "vendor_device_id", "name", "model",
            "serial", "mac", "status", "ip_address", "firmware",
            "site_id", "site_name", "tags", "raw", "collected_at",
        ]
        for key in required:
            self.assertIn(key, d, f"missing key: {key}")

    def test_to_dict_values(self):
        d = self._make_device().to_dict()
        self.assertEqual(d["vendor"], "meraki")
        self.assertEqual(d["status"], "online")
        self.assertIsInstance(d["tags"], list)
        self.assertIsInstance(d["collected_at"], str)  # ISO-8601 string

    def test_to_dict_raw_present(self):
        d = self._make_device().to_dict()
        self.assertIsInstance(d["raw"], dict)


# ──────────────────────────────────────────────────────────────────────────────
# VendorAlert model
# ──────────────────────────────────────────────────────────────────────────────

class TestVendorAlertModel(unittest.TestCase):
    def _make_alert(self):
        return VendorAlert(
            integration_id="meraki-org1",
            vendor="meraki",
            alert_id="alert-abc123",
            severity="high",
            category="connectivity",
            device_id="SERIAL123",
            device_name="AP-01",
            message="Device went offline",
            occurred_at=datetime(2024, 1, 15, 12, 0, 0, tzinfo=timezone.utc),
            resolved=False,
            raw={"raw_data": True},
        )

    def test_to_dict_has_required_keys(self):
        d = self._make_alert().to_dict()
        required = [
            "integration_id", "vendor", "alert_id", "severity", "category",
            "device_id", "device_name", "message", "occurred_at",
            "resolved", "raw",
        ]
        for key in required:
            self.assertIn(key, d, f"missing key: {key}")

    def test_to_dict_severity_values(self):
        for severity in ("critical", "high", "medium", "low", "info"):
            alert = VendorAlert(
                integration_id="x",
                vendor="meraki",
                alert_id="id",
                severity=severity,
                category="connectivity",
                device_id="",
                device_name="",
                message="",
                occurred_at=datetime.now(timezone.utc),
            )
            self.assertEqual(alert.to_dict()["severity"], severity)

    def test_to_dict_occurred_at_is_iso_string(self):
        d = self._make_alert().to_dict()
        self.assertIsInstance(d["occurred_at"], str)
        self.assertIn("2024", d["occurred_at"])

    def test_to_dict_resolved_flag(self):
        d = self._make_alert().to_dict()
        self.assertFalse(d["resolved"])


# ──────────────────────────────────────────────────────────────────────────────
# MerakiPlugin.parse_webhook
# ──────────────────────────────────────────────────────────────────────────────

class TestMerakiPluginWebhook(unittest.TestCase):
    def _valid_payload(self, secret: str = "s3cr3t") -> dict:
        return {
            "version": "0.1",
            "sharedSecret": secret,
            "sentAt": "2024-01-15T12:00:00.000000Z",
            "organizationId": "111111",
            "networkId": "N_12345",
            "networkName": "HQ",
            "alertType": "unreachable_device",
            "alertData": {
                "deviceSerial": "SERIAL123",
                "deviceName": "AP-01",
            },
        }

    def test_valid_payload_correct_secret_returns_alert(self):
        plugin = _meraki_plugin(webhook_secret="s3cr3t")
        result = plugin.parse_webhook(self._valid_payload("s3cr3t"), "1.2.3.4")
        self.assertEqual(len(result), 1)
        self.assertIsInstance(result[0], VendorAlert)

    def test_valid_payload_alert_has_correct_fields(self):
        plugin = _meraki_plugin(webhook_secret="s3cr3t")
        alert = plugin.parse_webhook(self._valid_payload("s3cr3t"), "1.2.3.4")[0]
        self.assertEqual(alert.vendor, "meraki")
        self.assertEqual(alert.integration_id, "meraki-org1")
        self.assertEqual(alert.severity, "high")   # unreachable_device → high
        self.assertEqual(alert.device_id, "SERIAL123")
        self.assertEqual(alert.device_name, "AP-01")

    def test_wrong_secret_returns_empty_list(self):
        plugin = _meraki_plugin(webhook_secret="s3cr3t")
        result = plugin.parse_webhook(self._valid_payload("WRONG"), "1.2.3.4")
        self.assertEqual(result, [])

    def test_empty_secret_in_creds_skips_verification(self):
        """If no webhook_secret configured, accept all webhooks."""
        plugin = _meraki_plugin(webhook_secret="")
        result = plugin.parse_webhook(self._valid_payload("anything"), "1.2.3.4")
        self.assertEqual(len(result), 1)

    def test_non_dict_payload_returns_empty(self):
        plugin = _meraki_plugin()
        result = plugin.parse_webhook("not-a-dict", "1.2.3.4")  # type: ignore[arg-type]
        self.assertEqual(result, [])

    def test_alert_occurred_at_is_datetime(self):
        plugin = _meraki_plugin(webhook_secret="s3cr3t")
        alert = plugin.parse_webhook(self._valid_payload("s3cr3t"), "1.2.3.4")[0]
        self.assertIsInstance(alert.occurred_at, datetime)

    def test_severity_mapping_critical(self):
        plugin = _meraki_plugin(webhook_secret="")
        payload = self._valid_payload("")
        payload["alertType"] = "gateway_to_internet_disconnected"
        alert = plugin.parse_webhook(payload, "1.2.3.4")[0]
        self.assertEqual(alert.severity, "critical")

    def test_unknown_alert_type_maps_to_info(self):
        plugin = _meraki_plugin(webhook_secret="")
        payload = self._valid_payload("")
        payload["alertType"] = "something_brand_new"
        alert = plugin.parse_webhook(payload, "1.2.3.4")[0]
        self.assertEqual(alert.severity, "info")


# ──────────────────────────────────────────────────────────────────────────────
# MistPlugin.parse_webhook
# ──────────────────────────────────────────────────────────────────────────────

class TestMistPluginWebhook(unittest.TestCase):
    def _device_event_payload(self) -> dict:
        return {
            "topic": "device-events",
            "events": [
                {
                    "id": "evt-001",
                    "type": "AP_DISCONNECTED",
                    "severity": "major",
                    "device_id": "ap-aabbccdd1122",
                    "device_name": "AP-Floor2",
                    "text": "AP disconnected from controller",
                    "timestamp": 1705320000,
                }
            ],
        }

    def _alarms_payload(self) -> dict:
        return {
            "topic": "alarms",
            "events": [
                {
                    "id": "alarm-xyz",
                    "severity": "critical",
                    "device_id": "sw-aabbccddeeff",
                    "device_name": "SW-Core",
                    "text": "Switch unreachable",
                    "timestamp": 1705320000,
                }
            ],
        }

    def _audits_payload(self) -> dict:
        return {
            "topic": "audits",
            "events": [
                {
                    "id": "audit-001",
                    "message": "Config changed by admin",
                    "timestamp": 1705320000,
                }
            ],
        }

    def test_device_events_topic_returns_vendor_alert(self):
        plugin = _mist_plugin()
        result = plugin.parse_webhook(self._device_event_payload(), "10.0.0.1")
        self.assertEqual(len(result), 1)
        self.assertIsInstance(result[0], VendorAlert)

    def test_device_events_severity_mapping(self):
        plugin = _mist_plugin()
        alert = plugin.parse_webhook(self._device_event_payload(), "10.0.0.1")[0]
        self.assertEqual(alert.severity, "high")  # "major" → "high"

    def test_device_events_alert_fields(self):
        plugin = _mist_plugin()
        alert = plugin.parse_webhook(self._device_event_payload(), "10.0.0.1")[0]
        self.assertEqual(alert.vendor, "mist")
        self.assertEqual(alert.device_id, "ap-aabbccdd1122")
        self.assertEqual(alert.device_name, "AP-Floor2")
        self.assertEqual(alert.message, "AP disconnected from controller")

    def test_alarms_topic_returns_alert(self):
        plugin = _mist_plugin()
        result = plugin.parse_webhook(self._alarms_payload(), "10.0.0.1")
        self.assertEqual(len(result), 1)
        self.assertIsInstance(result[0], VendorAlert)
        self.assertEqual(result[0].severity, "critical")

    def test_audits_topic_returns_config_alert(self):
        plugin = _mist_plugin()
        result = plugin.parse_webhook(self._audits_payload(), "10.0.0.1")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].category, "config")
        self.assertEqual(result[0].severity, "info")

    def test_empty_events_list_returns_empty(self):
        plugin = _mist_plugin()
        result = plugin.parse_webhook({"topic": "device-events", "events": []}, "10.0.0.1")
        self.assertEqual(result, [])

    def test_non_dict_payload_returns_empty(self):
        plugin = _mist_plugin()
        result = plugin.parse_webhook(None, "10.0.0.1")  # type: ignore[arg-type]
        self.assertEqual(result, [])

    def test_multiple_events_all_returned(self):
        plugin = _mist_plugin()
        payload = {
            "topic": "device-events",
            "events": [
                {"id": "e1", "severity": "major", "device_id": "d1", "device_name": "AP1",
                 "text": "msg1", "timestamp": 1705320000},
                {"id": "e2", "severity": "minor", "device_id": "d2", "device_name": "AP2",
                 "text": "msg2", "timestamp": 1705320001},
            ],
        }
        result = plugin.parse_webhook(payload, "10.0.0.1")
        self.assertEqual(len(result), 2)


# ──────────────────────────────────────────────────────────────────────────────
# UniFiPlugin.parse_webhook
# ──────────────────────────────────────────────────────────────────────────────

class TestUniFiPluginWebhook(unittest.TestCase):
    def test_parse_webhook_always_returns_empty(self):
        plugin = _unifi_plugin()
        result = plugin.parse_webhook({"any": "payload"}, "192.168.1.1")
        self.assertEqual(result, [])


# ──────────────────────────────────────────────────────────────────────────────
# Plugin registry
# ──────────────────────────────────────────────────────────────────────────────

class TestPluginRegistry(unittest.TestCase):
    def test_registry_contains_all_vendors(self):
        from ingest.plugins import PLUGIN_REGISTRY
        self.assertIn("meraki", PLUGIN_REGISTRY)
        self.assertIn("mist", PLUGIN_REGISTRY)
        self.assertIn("unifi", PLUGIN_REGISTRY)

    def test_registry_classes_are_plugins(self):
        from ingest.base_plugin import VendorAPIPlugin
        from ingest.plugins import PLUGIN_REGISTRY
        for name, cls in PLUGIN_REGISTRY.items():
            self.assertTrue(
                issubclass(cls, VendorAPIPlugin),
                f"{name} plugin does not subclass VendorAPIPlugin",
            )


# ──────────────────────────────────────────────────────────────────────────────
# VendorMetric model
# ──────────────────────────────────────────────────────────────────────────────

class TestVendorMetricModel(unittest.TestCase):
    def test_to_dict_has_required_keys(self):
        metric = VendorMetric(
            integration_id="meraki-org1",
            vendor="meraki",
            device_id="SERIAL123",
            metric_name="client_count",
            value=42.0,
            unit="count",
            tags={"site": "HQ"},
        )
        d = metric.to_dict()
        for key in ("integration_id", "vendor", "device_id", "metric_name",
                    "value", "unit", "tags", "timestamp"):
            self.assertIn(key, d, f"missing key: {key}")

    def test_to_dict_value_is_float(self):
        metric = VendorMetric(
            integration_id="x",
            vendor="meraki",
            device_id="d1",
            metric_name="latency_ms",
            value=12.5,
            unit="ms",
        )
        self.assertIsInstance(metric.to_dict()["value"], float)


if __name__ == "__main__":
    unittest.main()
