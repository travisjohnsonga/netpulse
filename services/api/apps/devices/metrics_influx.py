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
    # Universal CPU via HOST-RESOURCES hrProcessorLoad (resolved name or raw OID).
    "hrProcessorLoad_1": "cpu_pct",
    "1_3_6_1_2_1_25_3_3_1_2_1": "cpu_pct",
    # Cisco SNMP memory pool (kept for backward compatibility).
    "1_3_6_1_4_1_9_9_48_1_1_1_5_1": "memory_used_bytes",
    "1_3_6_1_4_1_9_9_48_1_1_1_6_1": "memory_free_bytes",
    "1_3_6_1_4_1_9_9_109_1_1_1_1_3_1": "cpu_1min_pct",
    "1_3_6_1_4_1_9_9_109_1_1_1_1_8_1": "cpu_5min_pct",
    # gNMI / Cisco IOS-XE memory-statistics subscription (Processor pool).
    "Processor/used_memory": "memory_used_bytes",
    "Processor/free_memory": "memory_free_bytes",
    "Processor/total_memory": "memory_total_bytes",
    # gNMI / Cisco IOS-XE cpu-utilization subscription (bare leaf names).
    "five_seconds": "cpu_5sec_pct",
    "one_minute": "cpu_1min_pct",
    "five_minutes": "cpu_5min_pct",
    # Fortinet FortiGate enterprise OIDs (FORTINET-FORTIGATE-MIB). The MIB
    # resolver doesn't know enterprise 12356, so the written field is the raw
    # OID form; the resolved-name aliases are kept too in case a MIB is loaded.
    "1_3_6_1_4_1_12356_101_4_1_3_0": "cpu_pct",          # fgSysCpuUsage (%)
    "fgSysCpuUsage_0": "cpu_pct",
    "1_3_6_1_4_1_12356_101_4_1_4_0": "memory_used_pct",  # fgSysMemUsage (already %)
    "fgSysMemUsage_0": "memory_used_pct",
    "1_3_6_1_4_1_12356_101_4_1_5_0": "memory_total_kb",  # fgSysMemCapacity (KB)
    "fgSysMemCapacity_0": "memory_total_kb",
    # SonicWall SonicOS (enterprise 8741) — CPU/mem already percentages.
    "1_3_6_1_4_1_8741_1_3_2_1_0": "cpu_pct",          # sonicCpuUtil
    "sonicCpuUtil_0": "cpu_pct",
    "1_3_6_1_4_1_8741_1_3_2_2_0": "memory_used_pct",  # sonicRamUtil
    "sonicRamUtil_0": "memory_used_pct",
    "1_3_6_1_4_1_8741_1_3_2_3_0": "memory_total_kb",  # sonicRamTotal
    "sonicRamTotal_0": "memory_total_kb",
    # Aruba AOS mobility controllers (enterprise 14823) — CPU/mem percentages.
    "1_3_6_1_4_1_14823_2_2_1_1_1_11_0": "cpu_pct",          # wlsxSysXCpuUtilization
    "wlsxSysXCpuUtilization_0": "cpu_pct",
    "1_3_6_1_4_1_14823_2_2_1_1_1_10_0": "memory_used_pct",  # wlsxSysXMemoryUsage
    "wlsxSysXMemoryUsage_0": "memory_used_pct",
    # Aruba AOS-CX native OpenConfig gNMI (dial-in, :8443).
    "/system/cpus/cpu/state/usage/instant": "cpu_pct",
    "/system/cpus/cpu[index=0]/state/usage/instant": "cpu_pct",
    "/system/memory/state/used": "memory_used_bytes",
    "/system/memory/state/free": "memory_free_bytes",
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
            "memory_free_bytes": None, "memory_total_bytes": None,
            "memory_used_pct": None, "cpu_pct": None, "poll_duration_ms": None,
        },
        "timeseries": {"uptime": [], "memory_used_pct": [], "cpu_pct": []},
        "interfaces": [],
        "environment": {},
        "reachability": _empty_reachability(),
    }


def _empty_reachability() -> dict:
    return {
        "current": None, "rtt_ms": None, "uptime_pct_24h": None,
        "avg_rtt_ms": None, "max_rtt_ms": None, "data": [],
    }


# Explicit environment scalars the stream-processor derives (apps.telemetry.
# snmp_environment). Excluded from the token-scan fallback so the count fields
# aren't themselves mistaken for sensors.
_EXPLICIT_ENV_KEYS = {"temp_max_c", "fan_count", "psu_count"}


def _environment(snapshot: dict) -> dict:
    """
    Surface temperature / fan / power-supply summary from the latest telemetry
    snapshot.

    Prefers the explicit scalars (`temp_max_c`, `fan_count`, `psu_count`) the
    stream-processor derives for ENTITY-SENSOR devices (AOS-CX). Falls back to
    token-scanning field names for gNMI environment paths that don't use them.

    Returns {} when no environment data is present — virtual platforms (e.g.
    Cisco C8000V) have no physical sensors and correctly report nothing, so the
    UI shows no fan/power/temperature tiles for them.
    """
    env: dict = {}
    temp_max = snapshot.get("temp_max_c")
    fan_count = snapshot.get("fan_count")
    psu_count = snapshot.get("psu_count")
    if any(isinstance(v, (int, float)) for v in (temp_max, fan_count, psu_count)):
        if isinstance(temp_max, (int, float)):
            env["temperature_c"] = round(temp_max, 1)
        if isinstance(fan_count, (int, float)):
            env["fan_count"] = int(fan_count)
        if isinstance(psu_count, (int, float)):
            env["psu_count"] = int(psu_count)
        return env

    temps, fans, powers = [], [], []
    for key, val in snapshot.items():
        if key in _EXPLICIT_ENV_KEYS or not isinstance(val, (int, float)):
            continue
        leaf = key.lower().rsplit("/", 1)[-1]
        if "temp" in leaf:
            temps.append(val)
        elif "fan" in leaf:
            fans.append(val)
        elif "power" in leaf or "psu" in leaf:
            powers.append(val)
    if temps:
        env["temperature_c"] = round(max(temps), 1)
        env["temperature_sensors"] = len(temps)
    if fans:
        env["fan_sensors"] = len(fans)
    if powers:
        env["power_sensors"] = len(powers)
    return env


def _environment_sensors(query_api, bucket, device_id, period) -> list:
    """Latest per-sensor temperature from the device_environment measurement."""
    flux = f'''
from(bucket: "{bucket}")
  |> range(start: -{period})
  |> filter(fn: (r) => r._measurement == "device_environment" and r.device_id == "{device_id}")
  |> last()
  |> pivot(rowKey: ["sensor_name"], columnKey: ["_field"], valueColumn: "_value")
'''
    out = []
    for table in query_api.query(flux):
        for rec in table.records:
            name = rec.values.get("sensor_name")
            if not name:
                continue
            t = rec.values.get("temperature_c")
            s = rec.values.get("status_ok")
            out.append({
                "sensor_name": name,
                "temperature_c": round(t, 1) if isinstance(t, (int, float)) else None,
                "status_ok": True if s is None else bool(s),
            })
    out.sort(key=lambda r: r["sensor_name"])
    return out


def _temperature_history(query_api, bucket, device_id) -> list:
    """Device max temperature per window over the last 24h (for the chart)."""
    window = _WINDOW.get("24h", "15m")
    flux = f'''
from(bucket: "{bucket}")
  |> range(start: -24h)
  |> filter(fn: (r) => r._measurement == "device_environment" and r.device_id == "{device_id}" and r._field == "temperature_c")
  |> group()
  |> aggregateWindow(every: {window}, fn: max, createEmpty: false)
'''
    out = []
    for table in query_api.query(flux):
        for rec in table.records:
            v = rec.get_value()
            if isinstance(v, (int, float)):
                out.append({"time": rec.get_time().isoformat().replace("+00:00", "Z"),
                            "value": round(v, 1)})
    out.sort(key=lambda p: p["time"])
    return out


def _pct_used(used, free):
    if used is None or free is None:
        return None
    total = used + free
    if total <= 0:
        return None
    return round(used / total * 100, 1)


def _mem_used_pct(used, free, total):
    """
    Memory utilisation %. Prefer an explicit total (gNMI reports total_memory);
    otherwise derive it from used + free (SNMP pool has no total).
    """
    if total and total > 0 and used is not None:
        return round(used / total * 100, 1)
    return _pct_used(used, free)


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
    total = snapshot.get("memory_total_bytes")
    # Prefer universal hrProcessorLoad (SNMP); else gNMI CPU, most-recent first.
    cpu = snapshot.get("cpu_pct")
    for key in ("cpu_5sec_pct", "cpu_1min_pct", "cpu_5min_pct"):
        if cpu is None:
            cpu = snapshot.get(key)
    # Prefer a directly-reported memory utilisation % (FortiGate fgSysMemUsage)
    # over the bytes-derived value (Cisco/gNMI report used+free/total bytes).
    mem_pct = snapshot.get("memory_used_pct")
    if mem_pct is None:
        mem_pct = _mem_used_pct(used, free, total)
    total_kb = snapshot.get("memory_total_kb")
    result["metrics"] = {
        "uptime_seconds": snapshot.get("uptime_seconds"),
        "memory_used_bytes": used,
        "memory_free_bytes": free,
        "memory_total_bytes": total if total is not None else (
            int(total_kb * 1024) if isinstance(total_kb, (int, float)) else None),
        "memory_used_pct": round(mem_pct, 1) if isinstance(mem_pct, (int, float)) else None,
        "cpu_pct": cpu,
        "poll_duration_ms": snapshot.get("poll_duration_ms"),
    }
    result["environment"] = _environment(snapshot)
    # Per-sensor temperatures + 24h history (device_environment measurement).
    if metric in ("all", "environment"):
        try:
            sensors = _environment_sensors(query_api, bucket, device_id, period)
            if sensors:
                result["environment"]["sensors"] = sensors
            history = _temperature_history(query_api, bucket, device_id)
            if history:
                result["environment"]["temperature_history"] = history
        except Exception as exc:
            logger.warning("environment sensor query failed for device %s: %s", device_id, exc)

    # ── per-interface derived stats (bps/pps/util/errors) ─────────────────────
    if metric in ("all", "interfaces"):
        try:
            result["interfaces"] = _interface_stats(query_api, bucket, device_id, period)
        except Exception as exc:
            logger.warning("interface_stats query failed for device %s: %s", device_id, exc)

    # ── timeseries ────────────────────────────────────────────────────────────
    result["timeseries"] = series

    # ── reachability / ping latency ───────────────────────────────────────────
    try:
        result["reachability"] = _reachability(query_api, bucket, device_id, period)
    except Exception as exc:
        logger.warning("reachability query failed for device %s: %s", device_id, exc)
    return result


def query_reachability(device_id: str, period: str = "1h") -> dict:
    """Standalone ping/RTT history for GET /api/devices/{id}/reachability/."""
    if period not in VALID_PERIODS:
        period = "1h"
    bucket = getattr(settings, "INFLUXDB_BUCKET", "metrics")
    out = {"device_id": device_id, "period": period, **_empty_reachability()}
    try:
        client = _client()
    except Exception as exc:
        logger.warning("InfluxDB client unavailable: %s", exc)
        return out
    try:
        out.update(_reachability(client.query_api(), bucket, device_id, period))
    except Exception as exc:
        logger.warning("reachability query failed for device %s: %s", device_id, exc)
    finally:
        try:
            client.close()
        except Exception:
            pass
    return out


def query_reachability_summary(period: str = "1h") -> dict:
    """
    Fleet active/unreachable counts over time for the dashboard "Device Status
    Over Time" chart. Takes the LAST is_reachable per device per window (so a
    device sampled twice a minute isn't double-counted) then sums across devices
    to get the active count; unreachable = total - active.
    """
    if period not in VALID_PERIODS:
        period = "1h"
    window = _WINDOW.get(period, "1m")
    bucket = getattr(settings, "INFLUXDB_BUCKET", "metrics")

    from .models import Device
    total = Device.objects.filter(
        status__in=[Device.Status.ACTIVE, Device.Status.UNREACHABLE]).count()
    out = {"period": period, "total_devices": total, "data": []}

    try:
        client = _client()
    except Exception as exc:
        logger.warning("InfluxDB client unavailable: %s", exc)
        return out

    flux = f'''
from(bucket: "{bucket}")
  |> range(start: -{period})
  |> filter(fn: (r) => r._measurement == "device_reachability" and r._field == "is_reachable")
  |> aggregateWindow(every: {window}, fn: last, createEmpty: false)
  |> group(columns: ["_time"])
  |> sum()
'''
    data = []
    try:
        for table in client.query_api().query(flux):
            for rec in table.records:
                active = int(rec.get_value() or 0)
                active = max(0, min(active, total))   # guard against clock-skew dupes
                t = rec.get_time().isoformat().replace("+00:00", "Z")
                data.append({"time": t, "active": active, "unreachable": max(0, total - active)})
    except Exception as exc:
        logger.warning("reachability summary query failed: %s", exc)
    finally:
        try:
            client.close()
        except Exception:
            pass
    out["data"] = sorted(data, key=lambda d: d["time"])
    return out


def _reachability(query_api, bucket, device_id, period) -> dict:
    """
    rtt_ms + is_reachable from the device_reachability measurement: current
    sample, period avg/max RTT, 24h uptime %, and a windowed series for charting.
    """
    window = _WINDOW.get(period, "1m")
    out = _empty_reachability()

    # Windowed series (mean rtt + mean reachable per bucket).
    series_flux = f'''
from(bucket: "{bucket}")
  |> range(start: -{period})
  |> filter(fn: (r) => r._measurement == "device_reachability" and r.device_id == "{device_id}")
  |> filter(fn: (r) => r._field == "rtt_ms" or r._field == "is_reachable")
  |> aggregateWindow(every: {window}, fn: mean, createEmpty: false)
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
'''
    data, rtts = [], []
    for table in query_api.query(series_flux):
        for rec in table.records:
            t = rec.get_time().isoformat().replace("+00:00", "Z")
            v = rec.values
            rtt = v.get("rtt_ms")
            reach = v.get("is_reachable")
            rtt = round(rtt, 2) if isinstance(rtt, (int, float)) else None
            reachable = (reach >= 0.5) if isinstance(reach, (int, float)) else None
            data.append({"time": t, "rtt_ms": rtt, "reachable": reachable})
            if rtt is not None and reachable:
                rtts.append(rtt)
    out["data"] = data
    if rtts:
        out["avg_rtt_ms"] = round(sum(rtts) / len(rtts), 2)
        out["max_rtt_ms"] = round(max(rtts), 2)
    # Latest sample (current state + rtt).
    for d in reversed(data):
        if d["reachable"] is not None:
            out["current"] = d["reachable"]
            out["rtt_ms"] = d["rtt_ms"]
            break
    # 24h uptime % = mean of is_reachable over the last 24h.
    up_flux = f'''
from(bucket: "{bucket}")
  |> range(start: -24h)
  |> filter(fn: (r) => r._measurement == "device_reachability" and r.device_id == "{device_id}")
  |> filter(fn: (r) => r._field == "is_reachable")
  |> mean()
'''
    for table in query_api.query(up_flux):
        for rec in table.records:
            val = rec.get_value()
            if isinstance(val, (int, float)):
                out["uptime_pct_24h"] = round(val * 100, 2)
    return out


_OPER = {1: "up", 2: "down", 3: "testing", 4: "unknown", 5: "dormant", 6: "notPresent", 7: "lowerLayerDown"}


def _iface_names(device_id: str) -> dict:
    """if_index → if_name from MonitoredInterface (best-effort)."""
    try:
        from apps.telemetry.models import MonitoredInterface
        return {str(ix): nm for ix, nm in MonitoredInterface.objects
                .filter(device_id=int(device_id))
                .values_list("if_index", "if_name") if ix is not None}
    except Exception:
        return {}


def _interface_stats(query_api, bucket, device_id, period) -> list:
    """Latest interface_stats per if_index + a short in/out bps series, with if_name."""
    names = _iface_names(device_id)
    window = _WINDOW.get(period, "1m")

    # Latest snapshot per if_index.
    snap_flux = f'''
from(bucket: "{bucket}")
  |> range(start: -{period})
  |> filter(fn: (r) => r._measurement == "interface_stats" and r.device_id == "{device_id}")
  |> last()
  |> pivot(rowKey: ["if_index"], columnKey: ["_field"], valueColumn: "_value")
'''
    by_idx: dict = {}
    for table in query_api.query(snap_flux):
        for rec in table.records:
            idx = rec.values.get("if_index")
            if idx is None:
                continue
            v = rec.values
            by_idx[idx] = {
                "if_index": idx,
                # gNMI tags if_index with the interface name itself (non-numeric);
                # fall back to it directly rather than the "if<idx>" SNMP form.
                "if_name": names.get(str(idx)) or (str(idx) if not str(idx).isdigit() else f"if{idx}"),
                "in_bps": v.get("in_bps"), "out_bps": v.get("out_bps"),
                "in_pps": v.get("in_pps"), "out_pps": v.get("out_pps"),
                "in_errors_rate": v.get("in_errors_rate"), "out_errors_rate": v.get("out_errors_rate"),
                "in_discards_rate": v.get("in_discards_rate"), "out_discards_rate": v.get("out_discards_rate"),
                "in_util_pct": v.get("in_util_pct"), "out_util_pct": v.get("out_util_pct"),
                "oper_status": _OPER.get(int(v["oper_status"]), "unknown") if isinstance(v.get("oper_status"), (int, float)) else None,
                "series": {"in_bps": [], "out_bps": []},
            }

    # in/out bps series for sparklines.
    series_flux = f'''
from(bucket: "{bucket}")
  |> range(start: -{period})
  |> filter(fn: (r) => r._measurement == "interface_stats" and r.device_id == "{device_id}")
  |> filter(fn: (r) => r._field == "in_bps" or r._field == "out_bps")
  |> aggregateWindow(every: {window}, fn: mean, createEmpty: false)
'''
    for table in query_api.query(series_flux):
        for rec in table.records:
            idx = rec.values.get("if_index")
            entry = by_idx.get(idx)
            if not entry:
                continue
            field = rec.get_field()
            val = rec.get_value()
            if field in ("in_bps", "out_bps") and isinstance(val, (int, float)):
                t = rec.get_time().isoformat().replace("+00:00", "Z")
                entry["series"][field].append({"time": t, "value": round(val, 2)})

    return sorted(by_idx.values(), key=lambda e: str(e["if_name"]))


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
            # Universal hrProcessorLoad first, then Cisco CPU OIDs.
            cpu = vals.get("hrProcessorLoad_1")
            if not isinstance(cpu, (int, float)):
                cpu = vals.get("1_3_6_1_2_1_25_3_3_1_2_1")
            if not isinstance(cpu, (int, float)):
                cpu = vals.get("1_3_6_1_4_1_9_9_109_1_1_1_1_8_1")
            if not isinstance(cpu, (int, float)):
                cpu = vals.get("1_3_6_1_4_1_9_9_109_1_1_1_1_3_1")
            if isinstance(cpu, (int, float)):
                cpu_pct.append({"time": t, "value": round(cpu, 1)})
    return {"uptime": uptime, "memory_used_pct": mem_pct, "cpu_pct": cpu_pct}
