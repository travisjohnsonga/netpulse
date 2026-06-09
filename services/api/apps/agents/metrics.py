"""
Agent metrics → InfluxDB.

Agent JSON payloads are turned into InfluxDB points using the SAME measurement
names as SNMP/REST collection (cpu/memory/disk/interface) so existing
dashboards work for agent-monitored servers too. ``build_points`` is pure (unit
tested); ``write_agent_metrics`` does the I/O and no-ops if InfluxDB is
unavailable (mirrors the stream-processor writer's best-effort behaviour).
"""
from __future__ import annotations

import logging
from typing import Any

from django.conf import settings

logger = logging.getLogger(__name__)


def _num(v: Any) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f


def build_points(device_id: int | str, hostname: str, metrics: dict) -> list[dict]:
    """Translate an agent metrics dict into InfluxDB points.

    Returns a list of ``{"measurement", "tags", "fields"}`` dicts (numeric
    fields only; empty fields are dropped). Pure — no I/O.
    """
    base = {"device_id": str(device_id), "hostname": hostname}
    points: list[dict] = []

    def add(measurement: str, tags: dict, raw_fields: dict) -> None:
        fields = {k: _num(v) for k, v in raw_fields.items()}
        fields = {k: v for k, v in fields.items() if v is not None}
        if fields:
            points.append({"measurement": measurement, "tags": {**base, **tags}, "fields": fields})

    cpu = metrics.get("cpu")
    for stat in cpu if isinstance(cpu, list) else []:
        if not isinstance(stat, dict):
            continue
        add("cpu", {"core": str(stat.get("core", "cpu"))}, {
            "usage_pct": stat.get("usage_pct"), "user": stat.get("user"),
            "system": stat.get("system"), "iowait": stat.get("iowait"),
            "steal": stat.get("steal"), "idle": stat.get("idle"),
        })

    load = metrics.get("load")
    if isinstance(load, dict):
        add("load", {}, {"load1": load.get("load1"), "load5": load.get("load5"),
                         "load15": load.get("load15")})

    mem = metrics.get("memory")
    if isinstance(mem, dict):
        add("memory", {}, {
            "total_bytes": mem.get("total_bytes"), "used_bytes": mem.get("used_bytes"),
            "free_bytes": mem.get("free_bytes"), "cached_bytes": mem.get("cached_bytes"),
            "available_bytes": mem.get("available_bytes"), "usage_pct": mem.get("usage_pct"),
            "swap_total": mem.get("swap_total_bytes"), "swap_used": mem.get("swap_used_bytes"),
        })

    disk = metrics.get("disk")
    for d in disk if isinstance(disk, list) else []:
        if not isinstance(d, dict):
            continue
        add("disk", {"mount": str(d.get("mount", "")), "device": str(d.get("device", ""))}, {
            "total_bytes": d.get("total_bytes"), "used_bytes": d.get("used_bytes"),
            "free_bytes": d.get("free_bytes"), "usage_pct": d.get("usage_pct"),
            "read_bytes_per_sec": d.get("read_bytes_per_sec"),
            "write_bytes_per_sec": d.get("write_bytes_per_sec"),
            "io_util_pct": d.get("io_util_pct"),
        })

    net = metrics.get("network")
    for n in net if isinstance(net, list) else []:
        if not isinstance(n, dict):
            continue
        add("interface", {"interface": str(n.get("interface", ""))}, {
            "rx_bytes": n.get("rx_bytes"), "tx_bytes": n.get("tx_bytes"),
            "rx_packets": n.get("rx_packets"), "tx_packets": n.get("tx_packets"),
            "rx_errors": n.get("rx_errors"), "tx_errors": n.get("tx_errors"),
            "rx_bps": n.get("rx_bps"), "tx_bps": n.get("tx_bps"),
        })

    return points


def write_agent_metrics(device_id, hostname: str, metrics: dict, ts=None) -> int:
    """Write agent metrics to InfluxDB. Returns the number of points written
    (0 if InfluxDB is unavailable). Never raises."""
    points = build_points(device_id, hostname, metrics)
    if not points:
        return 0
    try:
        from influxdb_client import InfluxDBClient, Point
        client = InfluxDBClient(
            url=getattr(settings, "INFLUXDB_URL", "http://influxdb:8086"),
            token=getattr(settings, "INFLUXDB_TOKEN", ""),
            org=getattr(settings, "INFLUXDB_ORG", "netpulse"),
            timeout=5_000,
        )
        bucket = getattr(settings, "INFLUXDB_BUCKET", "metrics")
        write_api = client.write_api()
        records = []
        for p in points:
            pt = Point(p["measurement"])
            for k, v in p["tags"].items():
                pt = pt.tag(k, v)
            for k, v in p["fields"].items():
                pt = pt.field(k, v)
            if ts:
                pt = pt.time(ts)
            records.append(pt)
        write_api.write(bucket=bucket, record=records)
        client.close()
    except Exception as exc:  # noqa: BLE001 — best-effort, like the stream writer
        logger.warning("agent metrics influx write failed: %s", exc)
        return 0
    return len(points)
