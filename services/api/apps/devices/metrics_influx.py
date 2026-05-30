"""
Read device telemetry back out of InfluxDB for the API/UI.

The stream-processor writes SNMP poll results to the ``telemetry`` measurement
in the ``metrics`` bucket, tagged by ``device_id``, with one field per polled
OID (dots → underscores; sysUpTime already converted to seconds). Here we read
the latest snapshot + a windowed time-series and map the raw OID field names to
human-readable metrics for the frontend.

All InfluxDB errors degrade gracefully to nulls/empty series — the Telemetry
tab must render even when InfluxDB is down or a device has no data yet.
"""
from __future__ import annotations

import logging

from django.conf import settings

logger = logging.getLogger(__name__)

VALID_PERIODS = {"1h", "6h", "24h", "7d"}

# Raw InfluxDB field name → friendly metric name.
FIELD_MAP = {
    "sysUpTime_0": "uptime_seconds",
    "1_3_6_1_4_1_9_9_48_1_1_1_5_1": "memory_used_bytes",
    "1_3_6_1_4_1_9_9_48_1_1_1_6_1": "memory_free_bytes",
    "1_3_6_1_4_1_9_9_109_1_1_1_1_3_1": "cpu_1min_pct",
    "1_3_6_1_4_1_9_9_109_1_1_1_1_8_1": "cpu_5min_pct",
    "poll_duration_ms": "poll_duration_ms",
}

# Coarser aggregation windows for longer ranges (keeps point counts sane).
_WINDOW = {"1h": "1m", "6h": "5m", "24h": "15m", "7d": "1h"}


def _client():
    from influxdb_client import InfluxDBClient
    return InfluxDBClient(
        url=getattr(settings, "INFLUXDB_URL", "http://influxdb:8086"),
        token=getattr(settings, "INFLUXDB_TOKEN", ""),
        org=getattr(settings, "INFLUXDB_ORG", "netpulse"),
        timeout=5_000,
    )


def _empty(device_id: str, period: str) -> dict:
    return {
        "device_id": device_id,
        "period": period,
        "metrics": {
            "uptime_seconds": None, "memory_used_bytes": None,
            "memory_free_bytes": None, "memory_used_pct": None,
            "cpu_pct": None, "poll_duration_ms": None,
        },
        "timeseries": {"uptime": [], "memory_used_pct": [], "cpu_pct": []},
        "interfaces": {},
    }


def _pct_used(used, free):
    if used is None or free is None:
        return None
    total = used + free
    if total <= 0:
        return None
    return round(used / total * 100, 1)


def query_device_metrics(device_id: str, metric: str = "all", period: str = "1h") -> dict:
    if period not in VALID_PERIODS:
        period = "1h"
    bucket = getattr(settings, "INFLUXDB_BUCKET", "metrics")
    result = _empty(device_id, period)

    try:
        client = _client()
    except Exception as exc:
        logger.warning("InfluxDB client unavailable: %s", exc)
        return result

    try:
        query_api = client.query_api()
        snapshot = _latest_snapshot(query_api, bucket, device_id, period)
        series = _timeseries(query_api, bucket, device_id, period)
    except Exception as exc:
        logger.warning("InfluxDB query failed for device %s: %s", device_id, exc)
        return result
    finally:
        try:
            client.close()
        except Exception:
            pass

    # ── snapshot ──────────────────────────────────────────────────────────────
    used = snapshot.get("memory_used_bytes")
    free = snapshot.get("memory_free_bytes")
    cpu = snapshot.get("cpu_5min_pct")
    if cpu is None:
        cpu = snapshot.get("cpu_1min_pct")
    result["metrics"] = {
        "uptime_seconds": snapshot.get("uptime_seconds"),
        "memory_used_bytes": used,
        "memory_free_bytes": free,
        "memory_used_pct": _pct_used(used, free),
        "cpu_pct": cpu,
        "poll_duration_ms": snapshot.get("poll_duration_ms"),
    }

    # Interface fields (ifHCInOctets_3 etc.) surfaced raw for the UI.
    interfaces = {k: v for k, v in snapshot.items()
                  if k.startswith(("ifHC", "ifIn", "ifOut", "ifOper"))}
    result["interfaces"] = interfaces

    # ── timeseries ────────────────────────────────────────────────────────────
    result["timeseries"] = series
    return result


def _latest_snapshot(query_api, bucket, device_id, period) -> dict:
    """Map of friendly_name/raw_field → latest value within the period."""
    flux = f'''
from(bucket: "{bucket}")
  |> range(start: -{period})
  |> filter(fn: (r) => r._measurement == "telemetry" and r.device_id == "{device_id}")
  |> last()
'''
    out: dict = {}
    for table in query_api.query(flux):
        for rec in table.records:
            field = rec.get_field()
            value = rec.get_value()
            if not isinstance(value, (int, float)):
                continue
            out[FIELD_MAP.get(field, field)] = value
    return out


def _timeseries(query_api, bucket, device_id, period) -> dict:
    """Windowed mean series for uptime, memory_used_pct and cpu_pct."""
    window = _WINDOW.get(period, "1m")
    flux = f'''
from(bucket: "{bucket}")
  |> range(start: -{period})
  |> filter(fn: (r) => r._measurement == "telemetry" and r.device_id == "{device_id}")
  |> aggregateWindow(every: {window}, fn: mean, createEmpty: false)
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
'''
    uptime, mem_pct, cpu_pct = [], [], []
    for table in query_api.query(flux):
        for rec in table.records:
            t = rec.get_time().isoformat().replace("+00:00", "Z")
            vals = rec.values
            up = vals.get("sysUpTime_0")
            if isinstance(up, (int, float)):
                uptime.append({"time": t, "value": round(up, 1)})
            used = vals.get("1_3_6_1_4_1_9_9_48_1_1_1_5_1")
            free = vals.get("1_3_6_1_4_1_9_9_48_1_1_1_6_1")
            p = _pct_used(used if isinstance(used, (int, float)) else None,
                          free if isinstance(free, (int, float)) else None)
            if p is not None:
                mem_pct.append({"time": t, "value": p})
            cpu = vals.get("1_3_6_1_4_1_9_9_109_1_1_1_1_8_1")
            if not isinstance(cpu, (int, float)):
                cpu = vals.get("1_3_6_1_4_1_9_9_109_1_1_1_1_3_1")
            if isinstance(cpu, (int, float)):
                cpu_pct.append({"time": t, "value": round(cpu, 1)})
    return {"uptime": uptime, "memory_used_pct": mem_pct, "cpu_pct": cpu_pct}
