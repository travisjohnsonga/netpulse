"""Circuit utilization from the bound interface's InfluxDB throughput.

interface_stats stores ``in_bps``/``out_bps`` tagged by ``if_index``; we map the
circuit's interface NAME to its if_index (via MonitoredInterface) then read the
series. rx = ingress/download (in_bps), tx = egress/upload (out_bps). Percentages
are against the circuit's configured bandwidth.
"""
from __future__ import annotations

import logging

from django.conf import settings

logger = logging.getLogger(__name__)


def _pct(mbps, bw):
    if not bw or mbps is None:
        return None
    return round(mbps / bw * 100, 1)


def _if_index_for(device_id: int, interface: str) -> str | None:
    """Resolve an interface NAME to its InfluxDB if_index tag for a device."""
    from apps.telemetry.models import MonitoredInterface
    mi = (MonitoredInterface.objects
          .filter(device_id=device_id, if_name=interface)
          .values("if_index").first())
    if mi and mi["if_index"] is not None:
        return str(mi["if_index"])
    # gNMI tags if_index with the interface name itself.
    return interface or None


def get_circuit_utilization(circuit, period: str = "24h") -> dict | None:
    """Return current/history/peak/p95 utilization, or None if not bound/queryable."""
    if not circuit.device_id or not circuit.interface:
        return None
    if_index = _if_index_for(circuit.device_id, circuit.interface)
    if not if_index:
        return None

    bw_down = circuit.bandwidth_mbps_download
    bw_up = circuit.upload_mbps

    from apps.devices.metrics_influx import _client
    bucket = settings.INFLUXDB_BUCKET
    flux = f'''
from(bucket: "{bucket}")
  |> range(start: -{period})
  |> filter(fn: (r) => r._measurement == "interface_stats" and r.device_id == "{circuit.device_id}" and r.if_index == "{if_index}")
  |> filter(fn: (r) => r._field == "in_bps" or r._field == "out_bps")
  |> aggregateWindow(every: 5m, fn: mean, createEmpty: false)
'''
    rx_series: dict[str, float] = {}
    tx_series: dict[str, float] = {}
    client = _client()
    try:
        for table in client.query_api().query(flux):
            for rec in table.records:
                if not isinstance(rec.get_value(), (int, float)):
                    continue
                t = rec.get_time().isoformat().replace("+00:00", "Z")
                mbps = round(rec.get_value() / 1_000_000, 2)
                (rx_series if rec.get_field() == "in_bps" else tx_series)[t] = mbps
    except Exception as exc:  # noqa: BLE001 — utilization is best-effort
        logger.warning("circuit %s utilization query failed: %s", circuit.id, exc)
        client.close()
        return _empty(circuit, bw_down, bw_up)
    finally:
        client.close()

    times = sorted(set(rx_series) | set(tx_series))
    history = [{
        "time": t,
        "rx_mbps": rx_series.get(t), "tx_mbps": tx_series.get(t),
        "rx_pct": _pct(rx_series.get(t), bw_down), "tx_pct": _pct(tx_series.get(t), bw_up),
    } for t in times]

    rx_vals = [v for v in rx_series.values()]
    tx_vals = [v for v in tx_series.values()]
    current = history[-1] if history else None
    peak = {
        "rx_mbps": max(rx_vals) if rx_vals else None,
        "rx_pct": _pct(max(rx_vals), bw_down) if rx_vals else None,
        "tx_mbps": max(tx_vals) if tx_vals else None,
        "tx_pct": _pct(max(tx_vals), bw_up) if tx_vals else None,
    }
    rx95, tx95 = _percentile(rx_vals, 95), _percentile(tx_vals, 95)
    p95 = {
        "rx_mbps": rx95, "rx_pct": _pct(rx95, bw_down),
        "tx_mbps": tx95, "tx_pct": _pct(tx95, bw_up),
    }
    return {
        "circuit_id": circuit.id, "name": circuit.name,
        "bandwidth_mbps_download": bw_down, "bandwidth_mbps_upload": bw_up,
        "current": current, "history": history, "peak": peak, "p95": p95,
    }


def _percentile(values: list[float], pct: int):
    """Nearest-rank percentile (standard for WAN 95th-percentile billing)."""
    if not values:
        return None
    s = sorted(values)
    # nearest-rank: ceil(pct/100 * n)
    import math
    rank = max(1, math.ceil(pct / 100 * len(s)))
    return round(s[rank - 1], 2)


def _empty(circuit, bw_down, bw_up) -> dict:
    return {
        "circuit_id": circuit.id, "name": circuit.name,
        "bandwidth_mbps_download": bw_down, "bandwidth_mbps_upload": bw_up,
        "current": None, "history": [], "peak": {}, "p95": {},
    }
