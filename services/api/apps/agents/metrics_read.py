"""Read agent (server) metrics back out of InfluxDB for the Servers UI.

Agents write the cpu/load/memory/disk/interface measurements (see metrics.py)
tagged by ``device_id``. Here we read the latest snapshot (for the list cards +
table) and windowed time-series (for the detail charts). Everything degrades to
empty/None on any InfluxDB error so the UI always renders.
"""
from __future__ import annotations

import logging

from django.conf import settings

logger = logging.getLogger(__name__)

RANGES = {"1h": "1m", "6h": "5m", "24h": "15m", "7d": "1h"}  # range → window


def _client():
    from influxdb_client import InfluxDBClient
    return InfluxDBClient(
        url=getattr(settings, "INFLUXDB_URL", "http://influxdb:8086"),
        token=getattr(settings, "INFLUXDB_TOKEN", ""),
        org=getattr(settings, "INFLUXDB_ORG", "netpulse"),
        timeout=5_000,
    )


def _bucket() -> str:
    return getattr(settings, "INFLUXDB_BUCKET", "metrics")


def latest_metrics(device_id: str) -> dict:
    """Summary for the Servers list: cpu/memory/load + worst disk mount."""
    out = {"cpu_pct": None, "memory_pct": None, "load_1": None,
           "disk_max_pct": None, "disk_max_mount": None}
    try:
        qa = _client().query_api()
        flux = f'''
from(bucket: "{_bucket()}")
  |> range(start: -10m)
  |> filter(fn: (r) => r.device_id == "{device_id}")
  |> filter(fn: (r) =>
       (r._measurement == "cpu" and r.core == "cpu" and r._field == "usage_pct") or
       (r._measurement == "memory" and r._field == "usage_pct") or
       (r._measurement == "load" and r._field == "load1") or
       (r._measurement == "disk" and r._field == "usage_pct"))
  |> last()
'''
        disk_max = None
        for table in qa.query(flux):
            for rec in table.records:
                m, f, val = rec.get_measurement(), rec.get_field(), rec.get_value()
                if val is None:
                    continue
                if m == "cpu":
                    out["cpu_pct"] = round(val, 1)
                elif m == "memory":
                    out["memory_pct"] = round(val, 1)
                elif m == "load":
                    out["load_1"] = round(val, 2)
                elif m == "disk":
                    if disk_max is None or val > disk_max:
                        disk_max = val
                        out["disk_max_pct"] = round(val, 1)
                        out["disk_max_mount"] = rec.values.get("mount")
    except Exception as exc:  # noqa: BLE001
        logger.debug("latest server metrics failed for %s: %s", device_id, exc)
    return out


def detail_metrics(device_id: str) -> dict:
    """Current snapshot for the detail page: per-core cpu, load, memory
    breakdown, per-mount disks, per-interface network."""
    result = {"cpu_pct": None, "cpu_cores": [], "load": {}, "memory": {},
              "disks": [], "interfaces": []}
    try:
        qa = _client().query_api()
        flux = f'''
from(bucket: "{_bucket()}")
  |> range(start: -10m)
  |> filter(fn: (r) => r.device_id == "{device_id}")
  |> filter(fn: (r) => r._measurement == "cpu" or r._measurement == "load" or
       r._measurement == "memory" or r._measurement == "disk" or r._measurement == "interface")
  |> last()
'''
        disks: dict = {}
        ifaces: dict = {}
        for table in qa.query(flux):
            for rec in table.records:
                m, f, val = rec.get_measurement(), rec.get_field(), rec.get_value()
                if val is None:
                    continue
                if m == "cpu":
                    core = rec.values.get("core", "cpu")
                    if f == "usage_pct":
                        if core == "cpu":
                            result["cpu_pct"] = round(val, 1)
                        else:
                            result["cpu_cores"].append({"core": core, "usage_pct": round(val, 1)})
                elif m == "load":
                    result["load"][f] = round(val, 2)
                elif m == "memory":
                    result["memory"][f] = val
                elif m == "disk":
                    mount = rec.values.get("mount", "")
                    disks.setdefault(mount, {"mount": mount, "device": rec.values.get("device", "")})[f] = val
                elif m == "interface":
                    name = rec.values.get("interface", "")
                    ifaces.setdefault(name, {"interface": name})[f] = val
        result["cpu_cores"].sort(key=lambda c: c["core"])
        result["disks"] = sorted(disks.values(), key=lambda d: d["mount"])
        result["interfaces"] = sorted(ifaces.values(), key=lambda i: i["interface"])
    except Exception as exc:  # noqa: BLE001
        logger.debug("detail server metrics failed for %s: %s", device_id, exc)
    return result


def metric_history(device_id: str, metric: str, rng: str = "1h") -> dict:
    """Windowed time-series for a single metric family. Returns
    ``{"metric", "range", "series": [{"t", <field>: value, ...}]}``."""
    window = RANGES.get(rng, "1m")
    spec = {
        "cpu": ("cpu", '(r.core == "cpu")', ["usage_pct", "user", "system", "iowait"]),
        "memory": ("memory", "true", ["usage_pct", "used_bytes", "cached_bytes", "free_bytes"]),
        "load": ("load", "true", ["load1", "load5", "load15"]),
        "disk": ("disk", "true", ["usage_pct"]),
        "network": ("interface", "true", ["rx_bps", "tx_bps"]),
    }.get(metric)
    if spec is None:
        return {"metric": metric, "range": rng, "series": []}
    measurement, extra_filter, fields = spec
    field_filter = " or ".join(f'r._field == "{f}"' for f in fields)
    # Per-mount/interface series keep their tag so the UI can split them.
    group_tag = {"disk": "mount", "network": "interface"}.get(metric)
    rows: dict = {}
    try:
        qa = _client().query_api()
        flux = f'''
from(bucket: "{_bucket()}")
  |> range(start: -{rng})
  |> filter(fn: (r) => r.device_id == "{device_id}" and r._measurement == "{measurement}" and {extra_filter})
  |> filter(fn: (r) => {field_filter})
  |> aggregateWindow(every: {window}, fn: mean, createEmpty: false)
'''
        for table in qa.query(flux):
            for rec in table.records:
                t = rec.get_time().isoformat()
                key = (t, rec.values.get(group_tag)) if group_tag else (t, None)
                row = rows.setdefault(key, {"t": t})
                if group_tag:
                    row[group_tag] = rec.values.get(group_tag)
                v = rec.get_value()
                row[rec.get_field()] = round(v, 2) if v is not None else None
    except Exception as exc:  # noqa: BLE001
        logger.debug("server metric history failed for %s/%s: %s", device_id, metric, exc)
    series = sorted(rows.values(), key=lambda r: (r.get(group_tag, "") if group_tag else "", r["t"]))
    return {"metric": metric, "range": rng, "series": series}
