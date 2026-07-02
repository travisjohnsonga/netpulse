"""Periodic AOS-CX environment + PoE collection → InfluxDB (alerting + trending).

The device Environment tab reads the ``device_environment`` measurement from
InfluxDB. This REST-based collector runs on the scheduler so temperatures, fans,
PSUs and PoE usage are stored even when nobody is viewing the tab — enabling a
standing **High PoE Usage** alert and historical trending.

It writes the SAME ``device_environment`` schema the query layer reads
(``apps.devices.metrics_influx._environment_detail``):

* temperature — tags ``{device_id, sensor_name, sensor_type=temperature}``,
  fields ``{temperature_c, status_ok}``
* fan — fields ``{fan_rpm, status}``
* psu — fields ``{watts, status}``
* poe summary — tags ``{…, sensor_name=poe, sensor_type=poe}``, fields
  ``{poe_budget_watts, poe_used_watts, poe_used_pct, poe_status}``
"""
from __future__ import annotations

import logging
import os

from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)

POE_ALERT_THRESHOLD_PCT = float(os.environ.get("POE_ALERT_THRESHOLD_PCT", "80"))
_POE_RULE_NAME = "High PoE Usage"

# AOS-CX sensor status strings that mean "healthy".
_OK_STATES = {"normal", "ok", "good", "online", ""}


def _status_ok(status: str | None) -> bool:
    return (status or "").strip().lower() in _OK_STATES


# ── PoE summary ──────────────────────────────────────────────────────────────
def poe_summary(poe_ports: list[dict]) -> dict | None:
    """Aggregate per-port PoE into a switch-level summary, or None if no PoE.

    ``used`` = Σ power drawn; ``budget`` = Σ power allocated (the reserved PSE
    budget AOS-CX exposes per port). ``used_pct`` is omitted when budget is 0.
    """
    if not poe_ports:
        return None
    used = 0.0
    budget = 0.0
    delivering = 0
    for p in poe_ports:
        drawn = p.get("power_drawn") or 0
        alloc = p.get("power_allocated") or 0
        used += float(drawn)
        budget += float(alloc)
        if drawn:
            delivering += 1
    summary = {
        "budget_watts": round(budget, 1),
        "used_watts": round(used, 1),
        "status": "delivering" if delivering else "idle",
        "ports_delivering": delivering,
    }
    if budget > 0:
        summary["used_pct"] = round(used / budget * 100, 1)
    return summary


# ── collection ───────────────────────────────────────────────────────────────
def collect_device_environment(device) -> dict | None:
    """REST-collect one AOS-CX device's environment + PoE. None on failure.

    Returns ``{temperatures, fans, power_supplies, poe}`` (poe may be None).
    """
    from apps.credentials import vault
    from apps.devices.aos_cx_client import AOSCXClient

    profile = device.credential_profile
    host = str(device.management_ip or device.ip_address or "")
    if not profile or not host:
        return None
    secrets = vault.read_secret(profile.vault_path) or {}
    username = profile.ssh_username or secrets.get("ssh_username", "")
    password = secrets.get("ssh_password", "")
    if not (username and password):
        return None
    try:
        with AOSCXClient(host) as client:
            client.login(username, password)
            env = client.get_environment()
            env["poe"] = poe_summary(client.get_poe_status())
            return env
    except Exception as exc:  # noqa: BLE001 — collection is best-effort
        logger.warning("environment poll failed for %s: %s", device.hostname, exc)
        return None


def _points_for(device, env: dict):
    """Build InfluxDB Points in the device_environment schema for one device."""
    from influxdb_client import Point

    did = str(device.id)
    ts = timezone.now()
    points = []

    for s in env.get("temperatures") or []:
        c = s.get("temperature_c")
        if c is None:
            continue
        points.append(
            Point("device_environment")
            .tag("device_id", did).tag("sensor_name", s.get("name") or "temp")
            .tag("sensor_type", "temperature")
            .field("temperature_c", float(c))
            .field("status_ok", 1 if _status_ok(s.get("status")) else 0)
            .time(ts))

    for f in env.get("fans") or []:
        rpm = f.get("rpm")
        points.append(
            Point("device_environment")
            .tag("device_id", did).tag("sensor_name", f.get("name") or "fan")
            .tag("sensor_type", "fan")
            .field("fan_rpm", float(rpm) if rpm is not None else -1.0)
            .field("status", "ok" if _status_ok(f.get("status")) else (f.get("status") or "fault"))
            .time(ts))

    for p in env.get("power_supplies") or []:
        w = p.get("instantaneous_power")
        points.append(
            Point("device_environment")
            .tag("device_id", did).tag("sensor_name", p.get("name") or "psu")
            .tag("sensor_type", "psu")
            .field("watts", float(w) if w is not None else -1.0)
            .field("status", "online" if _status_ok(p.get("status")) else (p.get("status") or "offline"))
            .time(ts))

    poe = env.get("poe")
    if poe:
        pt = (Point("device_environment")
              .tag("device_id", did).tag("sensor_name", "poe").tag("sensor_type", "poe")
              .field("poe_budget_watts", float(poe.get("budget_watts") or 0))
              .field("poe_used_watts", float(poe.get("used_watts") or 0))
              .field("poe_status", poe.get("status") or "unknown")
              .time(ts))
        if poe.get("used_pct") is not None:
            pt = pt.field("poe_used_pct", float(poe["used_pct"]))
        points.append(pt)

    return points


def _write_points(points) -> None:
    if not points:
        return
    from influxdb_client.client.write_api import SYNCHRONOUS

    from apps.devices.metrics_influx import _client
    client = _client()
    try:
        client.write_api(write_options=SYNCHRONOUS).write(
            bucket=settings.INFLUXDB_BUCKET, record=points)
    finally:
        client.close()


# ── PoE alert (standing, deduped, auto-resolving) ────────────────────────────
def _poe_rule():
    from apps.alerts.models import AlertRule
    rule, _ = AlertRule.objects.get_or_create(
        name=_POE_RULE_NAME,
        defaults={
            "description": "Warns when a switch's PoE power usage exceeds the "
                           "configured percentage of its budget.",
            "severity": AlertRule.Severity.MEDIUM,
            "condition": {"rule_type": "poe_usage", "metric": "poe_used_pct"},
            "cooldown_minutes": 0,
            "is_system": True,
        },
    )
    return rule


def reconcile_poe_alert(device, poe: dict | None, threshold: float = POE_ALERT_THRESHOLD_PCT) -> None:
    """Fire a standing alert while PoE usage is over threshold; resolve below."""
    from apps.alerts.models import AlertEvent

    pct = (poe or {}).get("used_pct")
    open_qs = AlertEvent.objects.filter(
        state=AlertEvent.State.FIRING,
        labels__alert_type="poe_usage",
        labels__device_id=device.id,
    )
    over = pct is not None and pct > threshold
    if not over:
        for ev in open_qs:
            ev.state = AlertEvent.State.RESOLVED
            ev.resolved_at = timezone.now()
            ev.resolution_note = "PoE usage back within threshold."
            ev.save(update_fields=["state", "resolved_at", "resolution_note"])
        return
    if open_qs.exists():
        return  # already firing — don't spam
    from apps.alerts.gating import rule_enabled
    rule = _poe_rule()
    if not rule_enabled(rule):
        return  # operator disabled the built-in → suppress new alerts
    used = poe.get("used_watts")
    budget = poe.get("budget_watts")
    AlertEvent.objects.create(
        rule=rule,
        state=AlertEvent.State.FIRING,
        labels={
            "source": "environment_poll", "device": device.hostname, "device_id": device.id,
            "hostname": device.hostname, "severity": "warning", "alert_type": "poe_usage",
            "poe_used_pct": str(pct),
        },
        annotations={
            "title": f"High PoE usage: {device.hostname} at {pct}%",
            "message": (
                f"{device.hostname} is using {used}W of {budget}W PoE budget ({pct}%), "
                f"over the {threshold:.0f}% threshold."),
            "severity": "warning",
        },
    )


# ── orchestration ────────────────────────────────────────────────────────────
def poll_environments() -> dict:
    """Collect environment + PoE for all active AOS-CX devices and store them.

    Returns a summary ``{devices, collected, points, poe_alerts}``.
    """
    from apps.devices.models import Device

    devices = (Device.objects
               .filter(status=Device.Status.ACTIVE, platform="aos_cx")
               .exclude(credential_profile__isnull=True)
               .select_related("credential_profile"))

    all_points = []
    collected = 0
    poe_over = 0
    for device in devices:
        env = collect_device_environment(device)
        if env is None:
            continue
        collected += 1
        all_points.extend(_points_for(device, env))
        try:
            reconcile_poe_alert(device, env.get("poe"))
            if (env.get("poe") or {}).get("used_pct", 0) and env["poe"]["used_pct"] > POE_ALERT_THRESHOLD_PCT:
                poe_over += 1
        except Exception as exc:  # noqa: BLE001 — alerting must not break collection
            logger.warning("PoE alert reconcile failed for %s: %s", device.hostname, exc)

    _write_points(all_points)
    if collected:
        logger.info("environment poll: %d device(s), %d point(s), %d PoE alert(s)",
                    collected, len(all_points), poe_over)
    return {"devices": len(devices), "collected": collected,
            "points": len(all_points), "poe_alerts": poe_over}
