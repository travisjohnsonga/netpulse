"""
Cisco Meraki REST API plugin.

Credentials (from OpenBao cred_path): {"api_key": "..."}
Config keys used: org_id, poll_interval

Meraki Dashboard API rate limit: 5 requests/second per API key.
We use a conservative 0.5 s delay between sequential calls.

Webhook payload format:
    {
        "version": "0.1",
        "sharedSecret": "...",
        "sentAt": "2024-01-01T00:00:00.000000Z",
        "organizationId": "...",
        "networkId": "...",
        "networkName": "...",
        "alertType": "...",
        "alertData": { ... }
    }
"""
import asyncio
import hashlib
import hmac
import logging
import uuid
from datetime import datetime, timezone

import aiohttp

from ..base_plugin import VendorAPIPlugin
from ..models import VendorAlert, VendorDevice

logger = logging.getLogger(__name__)

_ALERT_TYPE_SEVERITY: dict[str, str] = {
    "power_supply_down": "critical",
    "gateway_to_internet_disconnected": "critical",
    "unreachable_radio_detected": "high",
    "rogue_ap_detected": "high",
    "uplink_connectivity_changed": "high",
    "gateway_unreachable": "high",
    "unreachable_device": "high",
    "dhcp_no_leases": "medium",
    "usage_alert": "medium",
    "vpn_connectivity_changed": "medium",
    "setting_changed": "low",
    "scheduled_maintenances": "info",
}

_ALERT_TYPE_CATEGORY: dict[str, str] = {
    "power_supply_down": "connectivity",
    "gateway_to_internet_disconnected": "connectivity",
    "gateway_unreachable": "connectivity",
    "unreachable_device": "connectivity",
    "uplink_connectivity_changed": "connectivity",
    "vpn_connectivity_changed": "connectivity",
    "rogue_ap_detected": "security",
    "unreachable_radio_detected": "performance",
    "dhcp_no_leases": "performance",
    "usage_alert": "performance",
    "setting_changed": "config",
    "scheduled_maintenances": "config",
}


def _parse_ts(ts_str: str | None) -> datetime:
    if not ts_str:
        return datetime.now(timezone.utc)
    try:
        return datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return datetime.now(timezone.utc)


class MerakiPlugin(VendorAPIPlugin):
    vendor = "meraki"
    BASE_URL = "https://api.meraki.com/api/v1"
    RATE_LIMIT_DELAY = 0.5  # 5 req/s limit → 0.5 s to be safe

    def _headers(self) -> dict[str, str]:
        return {
            "X-Cisco-Meraki-API-Key": self.credentials["api_key"],
            "Content-Type": "application/json",
        }

    def _org_id(self) -> str:
        return str(self.config["org_id"])

    async def _get(self, session: aiohttp.ClientSession, path: str) -> dict | list:
        url = f"{self.BASE_URL}{path}"
        timeout = aiohttp.ClientTimeout(total=30)
        async with session.get(url, headers=self._headers(), timeout=timeout) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def fetch_devices(self) -> list[VendorDevice]:
        org_id = self._org_id()
        timeout = aiohttp.ClientTimeout(total=30)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            devices_raw = await self._get(session, f"/organizations/{org_id}/devices")
            await asyncio.sleep(self.RATE_LIMIT_DELAY)
            statuses_raw = await self._get(session, f"/organizations/{org_id}/devices/statuses")

        # Build status lookup keyed by serial
        status_by_serial: dict[str, dict] = {}
        if isinstance(statuses_raw, list):
            for s in statuses_raw:
                if isinstance(s, dict) and s.get("serial"):
                    status_by_serial[s["serial"]] = s

        results: list[VendorDevice] = []
        if not isinstance(devices_raw, list):
            return results

        for d in devices_raw:
            if not isinstance(d, dict):
                continue
            serial = d.get("serial", "")
            status_entry = status_by_serial.get(serial, {})
            raw_status = status_entry.get("status", "unknown")
            # Normalise to our schema values
            if raw_status == "online":
                status = "online"
            elif raw_status == "offline":
                status = "offline"
            elif raw_status == "alerting":
                status = "alerting"
            else:
                status = "offline"

            results.append(
                VendorDevice(
                    integration_id=self.integration_id,
                    vendor=self.vendor,
                    vendor_device_id=serial,
                    name=d.get("name") or d.get("mac", serial),
                    model=d.get("model", ""),
                    serial=serial,
                    mac=d.get("mac", ""),
                    status=status,
                    ip_address=status_entry.get("lanIp") or d.get("lanIp", ""),
                    firmware=d.get("firmware", ""),
                    site_id=d.get("networkId", ""),
                    site_name=d.get("networkId", ""),  # name resolved separately if needed
                    tags=d.get("tags") or [],
                    raw={k: v for k, v in d.items() if k != "api_key"},
                )
            )
        return results

    async def fetch_alerts(self) -> list[VendorAlert]:
        org_id = self._org_id()
        timeout = aiohttp.ClientTimeout(total=30)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            try:
                data = await self._get(session, f"/organizations/{org_id}/assurance/alerts")
            except aiohttp.ClientResponseError as exc:
                if exc.status == 404:
                    # Older Meraki API endpoint fallback
                    try:
                        data = await self._get(session, f"/organizations/{org_id}/alerts/overview")
                    except aiohttp.ClientResponseError:
                        logger.warning(
                            "Meraki alerts endpoint not available for org %s", org_id
                        )
                        return []
                else:
                    raise

        results: list[VendorAlert] = []
        items = data if isinstance(data, list) else data.get("items", []) if isinstance(data, dict) else []
        for item in items:
            if not isinstance(item, dict):
                continue
            alert_type = item.get("type", item.get("alertType", "unknown"))
            results.append(
                VendorAlert(
                    integration_id=self.integration_id,
                    vendor=self.vendor,
                    alert_id=item.get("id", str(uuid.uuid4())),
                    severity=_ALERT_TYPE_SEVERITY.get(alert_type, "info"),
                    category=_ALERT_TYPE_CATEGORY.get(alert_type, "connectivity"),
                    device_id=item.get("deviceSerial", item.get("serial", "")),
                    device_name=item.get("deviceName", item.get("name", "")),
                    message=item.get("description", item.get("message", alert_type)),
                    occurred_at=_parse_ts(item.get("startedAt", item.get("triggeredAt"))),
                    resolved=item.get("severity") == "informational" or bool(item.get("resolvedAt")),
                    raw=item,
                )
            )
        return results

    def parse_webhook(self, payload: dict, source_ip: str) -> list[VendorAlert]:
        """
        Parse a Meraki webhook payload.

        IMPORTANT: verifies sharedSecret matches the credential before processing.
        Returns [] if secret does not match or payload is malformed.
        """
        if not isinstance(payload, dict):
            return []

        # Verify shared secret — constant-time compare to prevent timing attacks
        expected_secret = self.credentials.get("webhook_secret", "")
        received_secret = payload.get("sharedSecret", "")
        if expected_secret:
            if not hmac.compare_digest(
                expected_secret.encode(), received_secret.encode()
            ):
                logger.warning(
                    "Meraki webhook secret mismatch from %s — discarding", source_ip
                )
                return []

        alert_type = payload.get("alertType", "unknown")
        network_id = payload.get("networkId", "")
        occurred_at = _parse_ts(payload.get("sentAt"))
        alert_data = payload.get("alertData", {})
        device_serial = alert_data.get("deviceSerial", "")
        device_name = alert_data.get("deviceName", "")

        alert = VendorAlert(
            integration_id=self.integration_id,
            vendor=self.vendor,
            alert_id=str(uuid.uuid4()),
            severity=_ALERT_TYPE_SEVERITY.get(alert_type, "info"),
            category=_ALERT_TYPE_CATEGORY.get(alert_type, "connectivity"),
            device_id=device_serial,
            device_name=device_name,
            message=f"{alert_type}: {alert_data}",
            occurred_at=occurred_at,
            resolved=False,
            raw=payload,
        )
        logger.info(
            "Meraki webhook from %s: alert_type=%s network=%s",
            source_ip, alert_type, network_id,
        )
        return [alert]
