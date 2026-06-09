"""
Collect per-AP telemetry from UniFi controllers.

UniFi access points are controller-managed (no SSH/SNMP), so their live radio
and health stats come from the controller's ``stat/device`` payload — the same
call inventory sync uses, but here we keep the rich per-radio detail that sync
discards. Each cycle (scheduler, every UNIFI_TELEMETRY_INTERVAL_S) we:

  - pull every AP for each enabled controller,
  - match it to a Device by IP (reusing the sync upsert so a brand-new AP still
    lands in inventory),
  - refresh the device's reachability/last_seen + the UnifiApStatus snapshot,
  - write rolling time-series points to InfluxDB:
      * ``unifi_ap_health`` — per-AP cpu/mem/temp/clients/satisfaction/uptime
      * ``unifi_ap_radio``  — per-AP-per-radio clients/util/noise/tx_power/bytes

Best-effort: an unreachable controller is logged and skipped, never raising from
collect_all_ap_telemetry. InfluxDB being down degrades to "snapshot only".
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# UniFi radio_table_stats 'name' → human band label.
RADIO_BAND = {"ng": "2.4GHz", "na": "5GHz", "6e": "6GHz"}


def _num(value, default=None):
    """Coerce to float, or return default for missing/non-numeric values."""
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _pct_retries(radio: dict) -> float | None:
    """tx_retries as a % of tx_packets (0 when no packets / unknown)."""
    pkts = _num(radio.get("tx_packets"), 0) or 0
    retries = _num(radio.get("tx_retries"), 0) or 0
    if pkts <= 0:
        return None
    return round(retries / pkts * 100, 2)


def map_unifi_ap(ap: dict) -> dict:
    """Normalize one UniFi AP ``stat/device`` dict to NetPulse telemetry shape.

    Returns a flat dict of health fields plus a ``radios`` list (one entry per
    radio band). Missing fields degrade to None/0 rather than raising — UniFi
    omits e.g. ``temperature`` on models without a sensor.
    """
    radios = []
    for radio in ap.get("radio_table_stats", []) or []:
        band = RADIO_BAND.get(radio.get("name", ""), radio.get("name", "") or "")
        radios.append({
            "band": band,
            "channel": radio.get("channel"),
            "channel_width": radio.get("channel_width", ""),
            "tx_power_dbm": _num(radio.get("tx_power")),
            "noise_floor_dbm": _num(radio.get("noise")),
            "clients": int(_num(radio.get("num_sta"), 0) or 0),
            "channel_utilization_pct": _num(radio.get("cu_total"), 0),
            "tx_retries_pct": _pct_retries(radio),
            "satisfaction": _num(radio.get("satisfaction")),
            "tx_bytes": int(_num(radio.get("tx_bytes"), 0) or 0),
            "rx_bytes": int(_num(radio.get("rx_bytes"), 0) or 0),
        })

    uplink = ap.get("uplink") or {}
    stat = ap.get("stat") or {}
    client_count = sum(r["clients"] for r in radios)
    if not radios:  # fall back to the top-level count if no radio detail
        client_count = int(_num(ap.get("num_sta"), 0) or 0)

    return {
        "mac": (ap.get("mac") or "").strip(),
        "name": (ap.get("name") or "").strip(),
        "ip": (ap.get("ip") or "").strip(),
        "model": (ap.get("model") or "").strip(),
        "version": (ap.get("version") or "").strip(),
        "state": int(_num(ap.get("state"), 0) or 0),
        "is_reachable": int(_num(ap.get("state"), 0) or 0) == 1,
        "uptime_seconds": int(_num(ap.get("uptime"), 0) or 0),
        "cpu_pct": _num(ap.get("cpu")),
        "memory_pct": _num(ap.get("mem")),
        "temperature_c": _num(ap.get("temperature")),
        "satisfaction": _num(ap.get("satisfaction")),
        "client_count": client_count,
        "uplink_speed_mbps": int(_num(uplink.get("speed"), 0) or 0) or None,
        "uplink_type": uplink.get("type", "") or "",
        "total_tx_bytes": int(_num(stat.get("tx_bytes"), 0) or 0),
        "total_rx_bytes": int(_num(stat.get("rx_bytes"), 0) or 0),
        "radios": radios,
    }


# ── InfluxDB time-series writes ─────────────────────────────────────────────
def _influx_points(device_id: str, m: dict, ts) -> list:
    """Build InfluxDB Points for one mapped AP: one health + one per radio."""
    from influxdb_client import Point

    points = []
    health = Point("unifi_ap_health").tag("device_id", device_id)
    for field in ("cpu_pct", "memory_pct", "temperature_c", "satisfaction",
                  "uptime_seconds", "client_count"):
        val = m.get(field)
        if val is not None:
            health = health.field(field, float(val))
    health = health.field("is_reachable", 1.0 if m["is_reachable"] else 0.0)
    health = health.field("tx_bytes", float(m["total_tx_bytes"]))
    health = health.field("rx_bytes", float(m["total_rx_bytes"]))
    if ts:
        health = health.time(ts)
    points.append(health)

    for radio in m["radios"]:
        p = (Point("unifi_ap_radio")
             .tag("device_id", device_id)
             .tag("radio_band", str(radio["band"]))
             .tag("channel", str(radio.get("channel") or ""))
             .field("clients", float(radio["clients"]))
             .field("channel_utilization_pct", float(radio.get("channel_utilization_pct") or 0))
             .field("tx_bytes", float(radio["tx_bytes"]))
             .field("rx_bytes", float(radio["rx_bytes"])))
        for field in ("noise_floor_dbm", "tx_power_dbm", "tx_retries_pct", "satisfaction"):
            val = radio.get(field)
            if val is not None:
                p = p.field(field, float(val))
        if ts:
            p = p.time(ts)
        points.append(p)
    return points


def _write_influx(all_points: list) -> None:
    """Write Points to InfluxDB (synchronous). No-op/best-effort on failure."""
    if not all_points:
        return
    from django.conf import settings
    try:
        from influxdb_client import InfluxDBClient
        from influxdb_client.client.write_api import SYNCHRONOUS
        client = InfluxDBClient(
            url=getattr(settings, "INFLUXDB_URL", "http://influxdb:8086"),
            token=getattr(settings, "INFLUXDB_TOKEN", ""),
            org=getattr(settings, "INFLUXDB_ORG", "netpulse"),
            timeout=5_000,
        )
        try:
            bucket = getattr(settings, "INFLUXDB_BUCKET", "metrics")
            client.write_api(write_options=SYNCHRONOUS).write(bucket=bucket, record=all_points)
        finally:
            client.close()
    except Exception as exc:  # noqa: BLE001 — telemetry must not break the cycle
        logger.warning("UniFi telemetry: InfluxDB write failed: %s", exc)


# ── snapshot persistence ─────────────────────────────────────────────────────
def _update_device_and_status(device, m: dict, controller, now) -> None:
    """Refresh the Device liveness fields + upsert its UnifiApStatus snapshot."""
    from .models import UnifiApStatus

    # Liveness only — model/version/hostname are owned by inventory sync.
    device.last_seen = now
    device.is_reachable = m["is_reachable"]
    device.save(update_fields=["last_seen", "is_reachable", "updated_at"])

    UnifiApStatus.objects.update_or_create(
        device=device,
        defaults={
            "controller": controller,
            "state": m["state"],
            "satisfaction": int(m["satisfaction"]) if m["satisfaction"] is not None else None,
            "client_count": m["client_count"],
            "cpu_pct": m["cpu_pct"],
            "memory_pct": m["memory_pct"],
            "temperature_c": m["temperature_c"],
            "uptime_seconds": m["uptime_seconds"],
            "uplink_speed_mbps": m["uplink_speed_mbps"],
            "uplink_type": m["uplink_type"],
            "radios": m["radios"],
            "last_collected": now,
        },
    )


def collect_controller_ap_telemetry(controller) -> dict:
    """Pull + persist AP telemetry for one controller. Returns counts.

    Raises UnifiError on connection failure (records last_error on the controller).
    """
    from django.utils import timezone

    from .unifi_client import UnifiClient, UnifiError
    from .unifi_sync import _credentials, _import_device

    counts = {"aps": 0, "matched": 0, "skipped": 0}
    try:
        username, password = _credentials(controller)
        with UnifiClient(controller.host, controller.port, username, password,
                         site_id=controller.unifi_site_id,
                         verify_ssl=controller.verify_ssl) as client:
            aps = client.get_ap_stats()
    except UnifiError as exc:
        controller.last_error = str(exc)[:512]
        controller.save(update_fields=["last_error", "updated_at"])
        raise

    now = timezone.now()
    points = []
    for raw in aps:
        counts["aps"] += 1
        # Ensure the AP exists in inventory (upsert via the sync path), then
        # look it up by IP to attach telemetry.
        _import_device(raw, controller)
        device = _match_device(raw)
        if device is None:
            counts["skipped"] += 1
            continue
        counts["matched"] += 1
        m = map_unifi_ap(raw)
        _update_device_and_status(device, m, controller, now)
        points.extend(_influx_points(str(device.id), m, now))

    _write_influx(points)
    logger.info("UniFi telemetry %s: %s", controller.name, counts)
    return counts


def _match_device(raw: dict):
    """Find the Device for a UniFi AP dict by IP (management_ip or ip_address)."""
    from django.db.models import Q

    from apps.devices.models import Device

    ip = (raw.get("ip") or "").strip()
    if not ip:
        return None
    return Device.objects.filter(Q(management_ip=ip) | Q(ip_address=ip)).first()


def collect_all_ap_telemetry() -> dict:
    """Collect AP telemetry for every enabled controller (best-effort)."""
    from .models import UnifiController

    totals = {"controllers": 0, "aps": 0, "matched": 0, "skipped": 0, "failed": 0}
    for controller in UnifiController.objects.filter(enabled=True):
        totals["controllers"] += 1
        try:
            c = collect_controller_ap_telemetry(controller)
            for k in ("aps", "matched", "skipped"):
                totals[k] += c[k]
        except Exception as exc:  # noqa: BLE001
            totals["failed"] += 1
            logger.warning("UniFi telemetry for %s failed: %s", controller.name, exc)
    return totals


# ── read-back for the device-detail Wireless tab (InfluxDB time-series) ──────
def query_ap_timeseries(device_id: str, period: str = "1h") -> dict:
    """Windowed client-count / channel-util / tx-rx-bytes series per radio band,
    plus an overall client-count series, for the AP charts. Degrades to empty
    series on any InfluxDB error."""
    from django.conf import settings

    valid = {"1h", "6h", "24h", "7d"}
    if period not in valid:
        period = "1h"
    window = {"1h": "1m", "6h": "5m", "24h": "15m", "7d": "1h"}[period]
    bucket = getattr(settings, "INFLUXDB_BUCKET", "metrics")

    empty = {"device_id": device_id, "period": period, "radios": {}, "clients_total": []}
    try:
        from influxdb_client import InfluxDBClient
    except Exception:  # noqa: BLE001
        return empty

    client = None
    try:
        client = InfluxDBClient(
            url=getattr(settings, "INFLUXDB_URL", "http://influxdb:8086"),
            token=getattr(settings, "INFLUXDB_TOKEN", ""),
            org=getattr(settings, "INFLUXDB_ORG", "netpulse"),
            timeout=5_000,
        )
        query_api = client.query_api()
        flux = f'''
from(bucket: "{bucket}")
  |> range(start: -{period})
  |> filter(fn: (r) => r._measurement == "unifi_ap_radio" and r.device_id == "{device_id}")
  |> filter(fn: (r) => r._field == "clients" or r._field == "channel_utilization_pct" or r._field == "tx_bytes" or r._field == "rx_bytes")
  |> aggregateWindow(every: {window}, fn: mean, createEmpty: false)
'''
        radios: dict[str, dict] = {}
        for table in query_api.query(flux):
            for rec in table.records:
                band = rec.values.get("radio_band", "") or ""
                field = rec.get_field()
                value = rec.get_value()
                if value is None:
                    continue
                t = rec.get_time().isoformat().replace("+00:00", "Z")
                band_d = radios.setdefault(band, {"clients": [], "channel_utilization_pct": [],
                                                  "tx_bytes": [], "rx_bytes": []})
                if field in band_d:
                    band_d[field].append({"time": t, "value": round(float(value), 2)})

        # Overall client count = sum across radios per timestamp.
        clients_total: dict[str, float] = {}
        for band_d in radios.values():
            for pt in band_d["clients"]:
                clients_total[pt["time"]] = clients_total.get(pt["time"], 0) + pt["value"]
        total_series = [{"time": t, "value": round(v, 1)} for t, v in sorted(clients_total.items())]

        return {"device_id": device_id, "period": period, "radios": radios,
                "clients_total": total_series}
    except Exception as exc:  # noqa: BLE001
        logger.warning("UniFi AP timeseries query failed: %s", exc)
        return empty
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:  # noqa: BLE001
                pass
