"""
Juniper Mist live location + usage for the warehouse dashboard.

Builds the single payload the Wireless-Location page polls, assembled entirely
server-side so the Mist token never leaves the api container (it's read from
OpenBao at call time, same as every other Mist call):

    map        floor-plan image + pixel dimensions, from /sites/{s}/maps
    aps        AP placements (x,y in PIXELS) + online status from /stats/devices
    clients    located WiFi clients (x,y in PIXELS) from /sites/{s}/stats/clients
    summary    clients online, APs up/total, throughput
    sle        a few wireless Service-Level success-rates (coverage, roaming, …)

Coordinate system (verified against a live gc4 org, not the JS-rendered docs):
Mist returns BOTH pixel coords (``x``/``y``) and metre coords (``x_m``/``y_m``)
on every located device/client; the map's ``width``/``height`` are PIXELS. We
emit the pixel coords and pixel map dimensions, so the frontend positions a
marker as a simple ``x / width`` percentage — no pixels-per-metre scaling.

Cadence note: this uses REST polling (driven by run_scheduler's `mist_location`
task, ~60s). Mist's located-client x/y also streams over the WebSocket channel
/sites/{s}/stats/maps/{m}/clients at ~6s; if the TV ever needs smoother motion,
swap the collector for a persistent WS listener (mirror run_stream_processor)
writing into the same cache — the REST contract below doesn't change.

WiFi clients only, by design — BLE/SDK channels are intentionally not consumed.
"""
from __future__ import annotations

import logging
import time

from django.core.cache import cache

logger = logging.getLogger(__name__)

# Cache key for the assembled dashboard payload, per (site, map).
_CACHE_KEY = "mist:location:{site}:{map}"
_CACHE_TTL = 300  # seconds; refreshed by the scheduler well inside this window.

# Wireless SLEs worth showing on a warehouse board. Roaming is first on purpose:
# Zebra handhelds walk the full aisle length, so inter-AP roaming is the metric
# most likely to expose a real coverage/handoff problem.
_SLE_METRICS = ["roaming", "coverage", "time-to-connect", "throughput"]


# ── Mist REST helpers ────────────────────────────────────────────────────────
# These reuse MistClient's authenticated session/_get (same package). Promote to
# MistClient methods if you prefer; kept here to avoid touching the vetted client.
def _maps(client, site_id: str) -> list:
    return client._get(f"/sites/{site_id}/maps", timeout=30) or []


def _ap_stats(client, site_id: str) -> list:
    """APs with live placement (map_id, x, y in pixels), name/mac, status, and
    client count. /stats/devices — NOT /devices — is the source: only the stats
    feed carries ``status`` ("connected") and the current num_clients."""
    return [d for d in (client.get_device_stats(site_id) or [])
            if (d.get("type") or "ap") == "ap"]


def _client_stats(client, site_id: str) -> list:
    return client._get(f"/sites/{site_id}/stats/clients", timeout=30) or []


def _sle_summary(client, site_id: str, metric: str):
    """Success rate (%) for one wireless SLE, or None if unavailable.

    Mist's metric-summary reports per-interval ``samples.total`` (client-minutes
    classified) and ``samples.degraded`` (the failing share); the SLE success
    rate is ``1 - degraded/total``. (The headline number is NOT a scalar
    ``value`` — ``value`` is the raw measurement, e.g. dBm for coverage, which is
    meaningless on a 0-100 gauge.) Never let an SLE hiccup break the board.
    """
    try:
        data = client._get(
            f"/sites/{site_id}/sle/site/{site_id}/metric/{metric}/summary",
            timeout=20,
        ) or {}
    except Exception as exc:  # noqa: BLE001
        logger.debug("mist sle %s failed: %s", metric, exc)
        return None
    samples = (data.get("sle") or {}).get("samples") or {}
    total = sum(s for s in (samples.get("total") or []) if isinstance(s, (int, float)))
    degraded = sum(s for s in (samples.get("degraded") or []) if isinstance(s, (int, float)))
    if total <= 0:
        return None
    return round((1 - degraded / total) * 100)


# ── Payload assembly ─────────────────────────────────────────────────────────
def _pick_map(maps: list, map_id: str | None) -> dict | None:
    if map_id:
        return next((m for m in maps if str(m.get("id")) == str(map_id)), None)
    return maps[0] if maps else None


def _map_meta(m: dict) -> dict:
    return {
        "id": str(m.get("id") or ""),
        "name": m.get("name") or "",
        "image_url": m.get("url") or "",          # signed floor-plan image URL
        "width": m.get("width") or 0,             # px
        "height": m.get("height") or 0,           # px
        "ppm": m.get("ppm") or 0,                 # pixels per metre (reference only)
    }


def _ap_markers(devices: list, map_id: str) -> list:
    out = []
    for d in devices:
        if str(d.get("map_id") or "") != str(map_id):
            continue
        if d.get("x") is None or d.get("y") is None:
            continue
        out.append({
            "name": d.get("name") or d.get("mac") or "",
            "mac": d.get("mac") or "",
            "x": d.get("x"),    # pixels on the map image
            "y": d.get("y"),
            "status": d.get("status") or "",
            "clients": d.get("num_clients"),
        })
    return out


def _client_throughput_kbps(c: dict) -> int:
    """Instantaneous per-client throughput in kbps.

    Mist exposes ACTUAL throughput as ``tx_bps``/``rx_bps`` (bits/sec) — distinct
    from ``tx_rate``/``rx_rate``, which are PHY link rates, NOT throughput. When
    both bps fields are absent (older/legacy stats object), fall back to a
    byte-delta: cache the cumulative ``tx_bytes+rx_bytes`` per client MAC between
    polls and divide the delta by the elapsed time. Null-safe; defaults to 0.
    """
    tx, rx = c.get("tx_bps"), c.get("rx_bps")
    if tx is not None or rx is not None:
        return round((float(tx or 0) + float(rx or 0)) / 1000)

    mac = c.get("mac")
    tx_b, rx_b = c.get("tx_bytes"), c.get("rx_bytes")
    if not mac or (tx_b is None and rx_b is None):
        return 0
    cur_bytes = float(tx_b or 0) + float(rx_b or 0)
    now = time.time()
    key = f"mist:client_bytes:{mac}"
    prev = cache.get(key)
    cache.set(key, (cur_bytes, now), 600)
    if not prev:
        return 0
    prev_bytes, prev_t = prev
    dt = now - prev_t
    if dt <= 0 or cur_bytes < prev_bytes:  # counter reset / no elapsed time
        return 0
    return round((cur_bytes - prev_bytes) * 8 / dt / 1000)  # bytes→bits, /s, →kbps


def _client_markers(clients: list, map_id: str) -> tuple[list, float]:
    """Located WiFi clients on this map (pixel x/y) with per-client throughput,
    plus a best-effort aggregate throughput across the whole site. Throughput
    uses ``tx_bps``/``rx_bps`` (real bits/s) — NOT ``tx_rate``/``rx_rate``, which
    are per-client PHY link rates."""
    out, tput_bps = [], 0.0
    for c in clients:
        # Site-wide throughput estimate (bits/s when present).
        tput_bps += float(c.get("tx_bps") or 0) + float(c.get("rx_bps") or 0)
        if str(c.get("map_id") or "") != str(map_id):
            continue
        if c.get("x") is None or c.get("y") is None:
            continue
        out.append({
            "mac": c.get("mac") or "",
            "name": c.get("hostname") or c.get("name") or c.get("mac") or "",
            "x": c.get("x"),                                  # pixels
            "y": c.get("y"),
            "band": str(c.get("band") or ""),
            "rssi": c.get("rssi"),
            "ap_mac": c.get("ap_mac") or "",
            "num_locating_aps": c.get("num_locating_aps"),
            "last_seen": c.get("last_seen"),
            "throughput_kbps": _client_throughput_kbps(c),
        })
    return out, round(tput_bps / 1_000_000, 1)  # → Mbps


def build_payload(client, site_id: str, map_id: str | None = None) -> dict:
    """Assemble the full dashboard contract for one site/map (one-shot REST)."""
    maps = _maps(client, site_id)
    m = _pick_map(maps, map_id)
    if not m:
        raise ValueError("No floor-plan map found for this Mist site.")
    meta = _map_meta(m)

    devices = _ap_stats(client, site_id)
    clients = _client_stats(client, site_id)

    aps = _ap_markers(devices, meta["id"])
    client_markers, tput_mbps = _client_markers(clients, meta["id"])

    on_map_aps = [d for d in devices if str(d.get("map_id") or "") == meta["id"]]
    aps_online = sum(1 for d in on_map_aps if d.get("status") == "connected")

    return {
        "map": meta,
        "aps": aps,
        "clients": client_markers,
        "summary": {
            "clients_online": len(client_markers),
            "clients_total": len([c for c in clients if str(c.get("map_id") or "") == meta["id"]]),
            "aps_online": aps_online,
            "aps_total": len(on_map_aps),
            "throughput_mbps": tput_mbps,
        },
        "sle": {metric.replace("-", "_"): _sle_summary(client, site_id, metric)
                for metric in _SLE_METRICS},
        "generated": int(time.time()),
    }


# ── Cache (written by the scheduler, read by the REST view) ──────────────────
def _mist_client():
    """A MistClient from the stored integration + OpenBao token, or None."""
    from .mist_client import MistClient, _read_api_token
    from .models import MistIntegration

    integ = MistIntegration.load()
    if not integ.enabled:
        return None, None
    token = _read_api_token()
    if not token:
        logger.warning("mist location: no API token in OpenBao")
        return None, None
    return MistClient(token, api_host=integ.api_host), integ


def refresh(site_id: str, map_id: str | None = None) -> dict | None:
    """Fetch + cache the dashboard payload. Called by run_scheduler and by the
    REST view's on-demand fallback. Returns None when Mist isn't configured."""
    client, _ = _mist_client()
    if client is None:
        return None
    payload = build_payload(client, site_id, map_id)
    key = _CACHE_KEY.format(site=site_id, map=payload["map"]["id"])
    cache.set(key, payload, _CACHE_TTL)
    return payload


def get_cached(site_id: str, map_id: str) -> dict | None:
    return cache.get(_CACHE_KEY.format(site=site_id, map=map_id))


def refresh_all() -> dict:
    """Scheduler entry point: refresh every synced Mist site's primary map."""
    from .models import MistSite

    refreshed = 0
    for s in MistSite.objects.all():
        try:
            if refresh(s.mist_id):
                refreshed += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning("mist location refresh failed for %s: %s", s.mist_id, exc)
    return {"sites_refreshed": refreshed}
