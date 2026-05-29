"""
Ubiquiti UniFi Network Application REST API plugin (self-hosted).

Credentials (from OpenBao cred_path):
    {
        "username": "...",
        "password": "...",
        "base_url": "https://unifi.local:8443",
        "verify_ssl": false           # optional, default false (self-signed certs)
    }
Config keys used: site (default "default"), poll_interval

IMPORTANT: UniFi commonly uses self-signed TLS certificates.
ssl=False is applied per-session based on the verify_ssl credential (default False).
SSL verification is never disabled globally.

UniFi does not support outbound webhooks — parse_webhook() always returns [].
"""
import logging
import ssl
import uuid
from datetime import datetime, timezone

import aiohttp

from ..base_plugin import VendorAPIPlugin
from ..models import VendorAlert, VendorDevice

logger = logging.getLogger(__name__)

_ALARM_KEY_SEVERITY: dict[str, str] = {
    "EVT_AP_Disconnected": "high",
    "EVT_SW_Disconnected": "high",
    "EVT_GW_Disconnected": "critical",
    "EVT_AP_Connected": "info",
    "EVT_SW_Connected": "info",
    "EVT_GW_Connected": "info",
    "EVT_LU_Detected": "medium",
    "EVT_WU_Detected": "medium",
}

_ALARM_KEY_CATEGORY: dict[str, str] = {
    "EVT_AP_Disconnected": "connectivity",
    "EVT_SW_Disconnected": "connectivity",
    "EVT_GW_Disconnected": "connectivity",
    "EVT_AP_Connected": "connectivity",
    "EVT_SW_Connected": "connectivity",
    "EVT_GW_Connected": "connectivity",
    "EVT_LU_Detected": "security",
    "EVT_WU_Detected": "security",
}


def _parse_ts(ts_val: int | str | None) -> datetime:
    if ts_val is None:
        return datetime.now(timezone.utc)
    if isinstance(ts_val, (int, float)):
        try:
            # UniFi uses milliseconds
            return datetime.fromtimestamp(ts_val / 1000, tz=timezone.utc)
        except (OSError, OverflowError, ValueError):
            return datetime.now(timezone.utc)
    return datetime.now(timezone.utc)


class UniFiPlugin(VendorAPIPlugin):
    vendor = "unifi"

    def _base_url(self) -> str:
        return self.credentials.get("base_url", "https://unifi.local:8443").rstrip("/")

    def _site(self) -> str:
        return self.config.get("site", "default")

    def _ssl_param(self):
        """
        Returns the ssl parameter for aiohttp requests.
        False = disable SSL verification (for self-signed certs).
        None = use default SSL context (verify enabled).
        Per-session, never globally.
        """
        verify = self.credentials.get("verify_ssl", False)
        if verify is False or str(verify).lower() == "false":
            return False
        return None  # aiohttp default: verify enabled

    async def _login(self, session: aiohttp.ClientSession) -> None:
        """Authenticate against UniFi controller and store session cookie."""
        url = f"{self._base_url()}/api/login"
        payload = {
            "username": self.credentials["username"],
            "password": self.credentials["password"],
        }
        timeout = aiohttp.ClientTimeout(total=30)
        async with session.post(
            url, json=payload, ssl=self._ssl_param(), timeout=timeout
        ) as resp:
            resp.raise_for_status()
            logger.debug("UniFi login successful for %s", self._base_url())

    async def fetch_devices(self) -> list[VendorDevice]:
        site = self._site()
        ssl_param = self._ssl_param()
        jar = aiohttp.CookieJar(unsafe=True)
        timeout = aiohttp.ClientTimeout(total=30)
        async with aiohttp.ClientSession(cookie_jar=jar, timeout=timeout) as session:
            await self._login(session)
            url = f"{self._base_url()}/api/s/{site}/stat/device"
            async with session.get(url, ssl=ssl_param) as resp:
                resp.raise_for_status()
                data = await resp.json()

        results: list[VendorDevice] = []
        devices_raw = data.get("data", []) if isinstance(data, dict) else []
        for d in devices_raw:
            if not isinstance(d, dict):
                continue
            state = d.get("state", 0)
            # UniFi device state: 1 = connected, 0 = disconnected
            status = "online" if state == 1 else "offline"
            results.append(
                VendorDevice(
                    integration_id=self.integration_id,
                    vendor=self.vendor,
                    vendor_device_id=d.get("device_id", d.get("_id", "")),
                    name=d.get("name", d.get("hostname", "")),
                    model=d.get("model", ""),
                    serial=d.get("serial", ""),
                    mac=d.get("mac", ""),
                    status=status,
                    ip_address=d.get("ip", ""),
                    firmware=d.get("version", ""),
                    site_id=d.get("site_id", site),
                    site_name=site,
                    tags=[],
                    raw=d,
                )
            )
        return results

    async def fetch_alerts(self) -> list[VendorAlert]:
        site = self._site()
        ssl_param = self._ssl_param()
        jar = aiohttp.CookieJar(unsafe=True)
        timeout = aiohttp.ClientTimeout(total=30)
        async with aiohttp.ClientSession(cookie_jar=jar, timeout=timeout) as session:
            await self._login(session)
            url = f"{self._base_url()}/api/s/{site}/stat/alarm"
            async with session.get(url, ssl=ssl_param) as resp:
                resp.raise_for_status()
                data = await resp.json()

        results: list[VendorAlert] = []
        alarms_raw = data.get("data", []) if isinstance(data, dict) else []
        for alarm in alarms_raw:
            if not isinstance(alarm, dict):
                continue
            key = alarm.get("key", "unknown")
            results.append(
                VendorAlert(
                    integration_id=self.integration_id,
                    vendor=self.vendor,
                    alert_id=alarm.get("_id", str(uuid.uuid4())),
                    severity=_ALARM_KEY_SEVERITY.get(key, "info"),
                    category=_ALARM_KEY_CATEGORY.get(key, "connectivity"),
                    device_id=alarm.get("device_id", alarm.get("ap", "")),
                    device_name=alarm.get("ap_name", alarm.get("sw_name", "")),
                    message=alarm.get("msg", key),
                    occurred_at=_parse_ts(alarm.get("datetime")),
                    resolved=bool(alarm.get("archived", False)),
                    raw=alarm,
                )
            )
        return results

    def parse_webhook(self, payload: dict, source_ip: str) -> list:
        """UniFi does not support outbound webhooks. Always returns []."""
        return []
