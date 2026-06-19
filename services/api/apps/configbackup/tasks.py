"""
Config-collection tasks.

Thin orchestration over apps.compliance.collector.collect_one (the real SSH
fetch + change-detection + store). Used by:
  - post-approval enrichment (collect_device_config — initial baseline)
  - the twice-daily scheduled run (collect_all_configs — drift detection)
  - run_config_manager (--once / --device-id manual collection)

When the scheduled run detects a changed config, it publishes a "Config Changed"
alert with the diff summary (best-effort NATS).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os

logger = logging.getLogger(__name__)


def collect_device_config(device_id, collected_by: str = "scheduled") -> dict:
    """Collect + store one device's running config. Returns the collector result."""
    from apps.compliance import collector
    from apps.devices.models import Device

    try:
        device = Device.objects.select_related("credential_profile").get(pk=device_id)
    except Device.DoesNotExist:
        logger.warning("collect_device_config: device %s not found", device_id)
        return {"ok": False, "error": "not_found"}
    return collector.collect_one(device, collected_by)


def collect_all_configs() -> dict:
    """
    Collect config from all active devices (the scheduled twice-daily run).
    Publishes a "Config Changed" alert per device whose config changed.
    """
    from apps.compliance import collector
    from apps.compliance.collector import SKIP_CONFIG_PLATFORMS
    from apps.devices.models import Device

    # Exclude cloud/controller-managed platforms (UniFi/Mist) and wireless roles
    # up front — they have no collectable config, so iterating them just wastes
    # connections and records false failures. collect_one also guards each call
    # (manual/enrichment paths) and returns skipped=True for anything that slips
    # through (e.g. a wireless device with an unexpected platform string).
    devices = list(
        Device.objects.filter(status=Device.Status.ACTIVE)
        .exclude(platform__in=SKIP_CONFIG_PLATFORMS)
        .exclude(role__slug__in=["wireless-ap", "wireless-controller"])
        .select_related("credential_profile", "role"))
    results = {"total": len(devices), "success": 0, "failed": 0,
               "unchanged": 0, "changed": 0, "skipped": 0}

    for device in devices:
        try:
            res = collector.collect_one(device, collected_by="scheduled")
        except Exception as exc:  # collect_one shouldn't raise, but never stop the loop
            logger.error("Config collection failed for %s: %s", device.hostname, exc)
            results["failed"] += 1
            continue
        if res.get("skipped"):
            results["skipped"] += 1
            continue
        if not res.get("ok"):
            results["failed"] += 1
            continue
        results["success"] += 1
        if res.get("changed"):
            results["changed"] += 1
            publish_config_change_alert(device, res)
        else:
            results["unchanged"] += 1

    logger.info("Config collection complete: %s", results)
    return results


def publish_config_change_alert(device, result: dict) -> None:
    """Best-effort NATS alert (rule 'Config Changed') with the diff in `details`."""
    cfg = result.get("config")
    diff = (getattr(cfg, "diff_summary", "") or "").strip()
    # Count +/- lines for a short, readable message; the full unified diff goes
    # in `details` so the UI can render it as a proper diff viewer.
    added = sum(1 for ln in diff.splitlines() if ln.startswith("+") and not ln.startswith("+++"))
    removed = sum(1 for ln in diff.splitlines() if ln.startswith("-") and not ln.startswith("---"))
    short = f"{added} line(s) added, {removed} removed" if diff else "configuration changed"
    payload = {
        "source": "config_manager", "rule_name": "Config Changed",
        "alert_type": "config_changed",
        "device_id": device.id, "hostname": device.hostname, "severity": "medium",
        "title": f"Config Changed: {device.hostname}",
        "message": f"{device.hostname}: {short}",
        "details": diff,
    }
    try:
        asyncio.run(_publish_alert(payload))
    except Exception as exc:
        logger.warning("config-change alert publish failed for %s: %s", device.hostname, exc)


async def _publish_alert(payload: dict) -> None:
    import nats  # lazy

    nc = await nats.connect(
        os.environ.get("NATS_URL", "nats://nats:4222"),
        user=os.environ.get("NATS_USER") or None,
        password=os.environ.get("NATS_PASSWORD") or None,
        connect_timeout=3,
    )
    try:
        await nc.publish(f"netpulse.alerts.{payload['severity']}", json.dumps(payload).encode())
        await nc.flush()
    finally:
        await nc.drain()
