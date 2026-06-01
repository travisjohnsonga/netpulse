"""
Determine HOW a device's telemetry is currently being collected — gNMI
streaming vs SNMP polling — by inspecting recent writes to InfluxDB's
``telemetry`` measurement, which the stream-processor tags with ``protocol``.

- gNMI is considered active when a ``protocol="gnmi"`` record arrived within
  ``GNMI_STALE_SECONDS``.
- SNMP is considered active when a ``protocol="snmp"`` record OR a
  ``poll_duration_ms`` field (which only the SNMP poller emits) arrived within
  ``SNMP_STALE_SECONDS``. The poll_duration_ms fallback keeps detection working
  for historical data written before the poller tagged its protocol.

All InfluxDB errors degrade gracefully to "inactive" — the device header and
Telemetry tab must render even when InfluxDB is down or the device has no data
yet. Configured intervals come from TelemetryConfig and the SNMP version from
the device's credential profile (the time-series itself carries neither).
"""
from __future__ import annotations

import logging

from django.conf import settings
from django.utils import timezone

from . import metrics_influx

logger = logging.getLogger(__name__)

# Staleness thresholds — roughly 2× the default sample/poll interval, so a
# single missed message doesn't flap the badge.
GNMI_STALE_SECONDS = 120   # default gNMI sample interval is 30s
SNMP_STALE_SECONDS = 600   # default SNMP poll interval is 300s


def _query_activity(device_id: str) -> dict:
    """
    Raw InfluxDB-derived activity for a device:
      gnmi_last_seen / snmp_last_seen — aware datetimes (or None)
      gnmi_field_count               — fields in the latest gNMI push (or None)

    Degrades to all-None on any InfluxDB error.
    """
    out: dict = {"gnmi_last_seen": None, "snmp_last_seen": None, "gnmi_field_count": None}
    bucket = getattr(settings, "INFLUXDB_BUCKET", "metrics")

    try:
        client = metrics_influx._client()
    except Exception as exc:
        logger.warning("InfluxDB client unavailable: %s", exc)
        return out

    try:
        query_api = client.query_api()
        # Latest telemetry record per protocol within the larger (SNMP) window.
        flux = f'''
from(bucket: "{bucket}")
  |> range(start: -{SNMP_STALE_SECONDS}s)
  |> filter(fn: (r) => r._measurement == "telemetry" and r.device_id == "{device_id}")
  |> group(columns: ["protocol"])
  |> last()
'''
        latest: dict = {}
        for table in query_api.query(flux):
            for rec in table.records:
                proto = rec.values.get("protocol") or "unknown"
                t = rec.get_time()
                if t and (proto not in latest or t > latest[proto]):
                    latest[proto] = t

        out["gnmi_last_seen"] = latest.get("gnmi")
        out["snmp_last_seen"] = latest.get("snmp")

        # SNMP fallback: the poll_duration_ms field is SNMP-only, so a recent one
        # means SNMP polling is active even if the record's protocol tag is the
        # legacy "unknown".
        if out["snmp_last_seen"] is None:
            poll_flux = f'''
from(bucket: "{bucket}")
  |> range(start: -{SNMP_STALE_SECONDS}s)
  |> filter(fn: (r) => r._measurement == "telemetry" and r.device_id == "{device_id}" and r._field == "poll_duration_ms")
  |> last()
'''
            for table in query_api.query(poll_flux):
                for rec in table.records:
                    t = rec.get_time()
                    if t and (out["snmp_last_seen"] is None or t > out["snmp_last_seen"]):
                        out["snmp_last_seen"] = t

        if out["gnmi_last_seen"] is not None:
            out["gnmi_field_count"] = _gnmi_field_count(query_api, bucket, device_id)
    except Exception as exc:
        logger.warning("collection-status query failed for device %s: %s", device_id, exc)
    finally:
        try:
            client.close()
        except Exception:
            pass

    return out


def _gnmi_field_count(query_api, bucket, device_id) -> int | None:
    """Number of distinct fields in the latest gNMI push (= metrics_per_push)."""
    flux = f'''
from(bucket: "{bucket}")
  |> range(start: -{GNMI_STALE_SECONDS}s)
  |> filter(fn: (r) => r._measurement == "telemetry" and r.device_id == "{device_id}" and r.protocol == "gnmi")
  |> last()
  |> group()
  |> count()
'''
    for table in query_api.query(flux):
        for rec in table.records:
            v = rec.get_value()
            if isinstance(v, (int, float)):
                return int(v)
    return None


def _seconds_ago(now, ts) -> int | None:
    if ts is None:
        return None
    return max(0, int((now - ts).total_seconds()))


def _gnmi_interval(device) -> int:
    cfg = getattr(device, "telemetry_config", None)
    return cfg.gnmi_interval if cfg and cfg.gnmi_interval else 30


def _snmp_interval(device) -> int:
    """Effective SNMP poll interval: per-device override else config else 300."""
    cfg = getattr(device, "telemetry_config", None)
    if cfg and cfg.override_intervals and cfg.device_metrics_interval:
        return cfg.device_metrics_interval
    if cfg and cfg.snmp_interval:
        return cfg.snmp_interval
    return 300


def _snmp_version(device) -> str | None:
    p = getattr(device, "credential_profile", None)
    if not p:
        return None
    if getattr(p, "snmpv3_enabled", False):
        return "v3"
    if getattr(p, "snmpv2c_enabled", False):
        return "v2c"
    return None


def build_collection_status(device, now=None) -> dict:
    """Compose the collection-status payload for a device (see module docstring)."""
    now = now or timezone.now()
    activity = _query_activity(str(device.id))

    gnmi_ago = _seconds_ago(now, activity.get("gnmi_last_seen"))
    gnmi_active = gnmi_ago is not None and gnmi_ago <= GNMI_STALE_SECONDS

    snmp_ago = _seconds_ago(now, activity.get("snmp_last_seen"))
    snmp_active = snmp_ago is not None and snmp_ago <= SNMP_STALE_SECONDS

    primary = "gnmi" if gnmi_active else ("snmp" if snmp_active else None)

    return {
        "device_id": str(device.id),
        "gnmi": {
            "active": gnmi_active,
            "last_seen_seconds_ago": gnmi_ago if gnmi_active else None,
            "metrics_per_push": activity.get("gnmi_field_count") if gnmi_active else None,
            "interval_seconds": _gnmi_interval(device),
        },
        "snmp": {
            "active": snmp_active,
            "last_poll_seconds_ago": snmp_ago if snmp_active else None,
            "interval_seconds": _snmp_interval(device),
            "version": _snmp_version(device),
        },
        "primary": primary,
        "any_active": gnmi_active or snmp_active,
    }
