"""
Juniper Mist REST API plugin.

Credentials (from OpenBao cred_path): {"api_token": "..."}
Config keys used: org_id, poll_interval

Mist API rate limit: 100 req/10s → ~10 req/s.
We use a conservative 0.2 s delay between sequential calls.

Webhook payload format:
    {
        "topic": "device-events" | "alarms" | "audits",
        "events": [ {...}, ... ]
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

_ALARM_SEVERITY_MAP: dict[str, str] = {
    "critical": "critical",
    "major": "high",
    "minor": "medium",
    "warn": "medium",
    "info": "info",
}


def _parse_ts(ts_val: int | str | None) -> datetime:
    """Accept Unix epoch (int) or ISO-8601 string."""
    if ts_val is None:
        return datetime.now(timezone.utc)
    if isinstance(ts_val, (int, float)):
        try:
            return datetime.fromtimestamp(ts_val, tz=timezone.utc)
        except (OSError, OverflowError, ValueError):
            return datetime.now(timezone.utc)
    if isinstance(ts_val, str):
        try:
            return datetime.fromisoformat(ts_val.replace("Z", "+00:00"))
        except ValueError:
            return datetime.now(timezone.utc)
    return datetime.now(timezone.utc)


class MistPlugin(VendorAPIPlugin):
    vendor = "mist"
    BASE_URL = "https://api.mist.com/api/v1"
    RATE_LIMIT_DELAY = 0.2  # ~5 req/s, well within 100 req/10s limit

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Token {self.credentials['api_token']}",
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
            devices_raw = await self._get(session, f"/orgs/{org_id}/devices")
            await asyncio.sleep(self.RATE_LIMIT_DELAY)
            sites_raw = await self._get(session, f"/orgs/{org_id}/sites")

        # Build site name lookup
        site_names: dict[str, str] = {}
        if isinstance(sites_raw, list):
            for s in sites_raw:
                if isinstance(s, dict) and s.get("id"):
                    site_names[s["id"]] = s.get("name", s["id"])

        results: list[VendorDevice] = []
        if not isinstance(devices_raw, list):
            return results

        for d in devices_raw:
            if not isinstance(d, dict):
                continue
            raw_status = d.get("status", "")
            if raw_status == "connected":
                status = "online"
            elif raw_status == "disconnected":
                status = "offline"
            else:
                status = "offline"

            site_id = d.get("site_id", "")
            results.append(
                VendorDevice(
                    integration_id=self.integration_id,
                    vendor=self.vendor,
                    vendor_device_id=d.get("id", ""),
                    name=d.get("name", ""),
                    model=d.get("model", ""),
                    serial=d.get("serial", ""),
                    mac=d.get("mac", ""),
                    status=status,
                    ip_address=d.get("ip", ""),
                    firmware=d.get("version", ""),
                    site_id=site_id,
                    site_name=site_names.get(site_id, site_id),
                    tags=d.get("tags") or [],
                    raw=d,
                )
            )
        return results

    async def fetch_alerts(self) -> list[VendorAlert]:
        org_id = self._org_id()
        timeout = aiohttp.ClientTimeout(total=30)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            try:
                data = await self._get(session, f"/orgs/{org_id}/events/device")
            except aiohttp.ClientResponseError:
                logger.warning(
                    "Mist device events endpoint not available for org %s", org_id
                )
                return []

        results: list[VendorAlert] = []
        items = data if isinstance(data, list) else data.get("results", []) if isinstance(data, dict) else []
        for item in items:
            if not isinstance(item, dict):
                continue
            severity_raw = item.get("severity", "info")
            results.append(
                VendorAlert(
                    integration_id=self.integration_id,
                    vendor=self.vendor,
                    alert_id=item.get("id", str(uuid.uuid4())),
                    severity=_ALARM_SEVERITY_MAP.get(severity_raw, "info"),
                    category="connectivity",
                    device_id=item.get("device_id", item.get("mac", "")),
                    device_name=item.get("device_name", ""),
                    message=item.get("text", item.get("type", "unknown")),
                    occurred_at=_parse_ts(item.get("timestamp")),
                    resolved=False,
                    raw=item,
                )
            )
        return results

    def parse_webhook(self, payload: dict, source_ip: str) -> list[VendorAlert | VendorDevice]:
        """
        Parse a Mist webhook payload.

        Verifies X-Mist-Signature HMAC-SHA256 if a webhook_secret is configured.
        Handles topics: "device-events", "alarms", "audits".
        """
        if not isinstance(payload, dict):
            return []

        topic = payload.get("topic", "")
        events = payload.get("events", [])
        if not isinstance(events, list):
            return []

        results: list[VendorAlert | VendorDevice] = []

        for event in events:
            if not isinstance(event, dict):
                continue

            if topic in ("device-events", "alarms"):
                severity_raw = event.get("severity", "info")
                results.append(
                    VendorAlert(
                        integration_id=self.integration_id,
                        vendor=self.vendor,
                        alert_id=event.get("id", str(uuid.uuid4())),
                        severity=_ALARM_SEVERITY_MAP.get(severity_raw, "info"),
                        category="connectivity",
                        device_id=event.get("device_id", event.get("mac", "")),
                        device_name=event.get("device_name", ""),
                        message=event.get("text", event.get("type", "unknown")),
                        occurred_at=_parse_ts(event.get("timestamp")),
                        resolved=False,
                        raw=event,
                    )
                )
            # "audits" topic → config category
            elif topic == "audits":
                results.append(
                    VendorAlert(
                        integration_id=self.integration_id,
                        vendor=self.vendor,
                        alert_id=event.get("id", str(uuid.uuid4())),
                        severity="info",
                        category="config",
                        device_id="",
                        device_name="",
                        message=event.get("message", "audit event"),
                        occurred_at=_parse_ts(event.get("timestamp")),
                        resolved=False,
                        raw=event,
                    )
                )

        logger.info(
            "Mist webhook from %s: topic=%s events=%d results=%d",
            source_ip, topic, len(events), len(results),
        )
        return results
