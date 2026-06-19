"""
Wireless AP fleet API — /api/wireless/.

Covers every wireless access point, regardless of vendor:
- UniFi APs come with rich telemetry (per-AP snapshots persisted by the
  UniFi-telemetry scheduler task, apps.integrations.unifi_telemetry →
  UnifiApStatus; rolling time-series in InfluxDB).
- Mist APs are currently inventory-only (imported by apps.integrations.mist_sync
  as platform 'mist_ap'); per-AP Mist telemetry is a planned follow-up, so they
  surface here built from the Device record alone (no radios/score yet).

Only access points belong on this page — switches/gateways/consoles
(mist_sw/mist_gw/unifi_sw/unifi_udm/…) live on the Network Devices page. These
endpoints serve the fleet overview (summary cards, AP table, channel heatmap).
"""
from __future__ import annotations

from collections import defaultdict

from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

# The only platforms shown on the Wireless page (access points). NOT switches/
# gateways/consoles — those are Network Devices.
WIRELESS_AP_PLATFORMS = ["unifi_ap", "mist_ap"]


def wireless_source(platform: str) -> str:
    """Coarse vendor key for badging/filtering: 'unifi' | 'mist' | ''."""
    p = (platform or "").lower()
    if p.startswith("mist"):
        return "mist"
    if p.startswith("unifi"):
        return "unifi"
    return ""


def wireless_vendor(platform: str) -> str:
    """Human vendor label for the AP table."""
    return {"unifi": "UniFi", "mist": "Mist"}.get(wireless_source(platform), "")


def _ap_queryset():
    from .models import UnifiApStatus
    return (UnifiApStatus.objects
            .select_related("device", "device__site", "controller")
            .order_by("device__hostname"))


def _serialize_aps(qs):
    from .serializers import UnifiApStatusSerializer
    return UnifiApStatusSerializer(qs, many=True).data


def _device_ap_entry(device) -> dict:
    """A wireless-AP row built from a Device alone (no telemetry snapshot) — used
    for AP platforms we inventory but don't yet collect AP telemetry for (Mist).
    Mirrors the UnifiApStatusSerializer shape so the frontend treats them
    uniformly; telemetry-only fields are null/empty."""
    platform = device.platform or ""
    return {
        "device_id": device.id,
        "hostname": device.hostname,
        "ip_address": device.ip_address,
        "model": device.model or "",
        "os_version": device.os_version or "",
        "site_name": device.site.name if device.site_id else None,
        "controller_name": None,
        "source": wireless_source(platform),
        "vendor": wireless_vendor(platform),
        # No live telemetry yet: derive online/offline from device reachability.
        "state": 1 if device.is_reachable else 0,
        "satisfaction": None,
        "client_count": 0,
        "cpu_pct": None,
        "memory_pct": None,
        "temperature_c": None,
        "uptime_seconds": None,
        "uplink_speed_mbps": None,
        "uplink_type": "",
        "radios": [],
        "last_collected": None,
    }


def _all_ap_entries() -> list:
    """Every wireless AP across vendors: UniFi telemetry snapshots plus inventory
    rows for AP platforms without a snapshot (Mist, or a not-yet-polled UniFi AP)."""
    from apps.devices.models import Device

    aps = list(_serialize_aps(_ap_queryset()))
    covered = {a["device_id"] for a in aps}
    extras = (Device.objects
              .filter(platform__in=WIRELESS_AP_PLATFORMS)
              .exclude(id__in=covered)
              .select_related("site")
              .order_by("hostname"))
    aps.extend(_device_ap_entry(d) for d in extras)
    aps.sort(key=lambda a: (a["hostname"] or "").lower())
    return aps


def _summary(aps: list) -> dict:
    online = sum(1 for a in aps if a["state"] == 1)
    clients = sum(a["client_count"] or 0 for a in aps)
    scores = [a["satisfaction"] for a in aps if a["satisfaction"] is not None]
    return {
        "total_aps": len(aps),
        "online": online,
        "offline": len(aps) - online,
        "total_clients": clients,
        "avg_satisfaction": round(sum(scores) / len(scores)) if scores else None,
    }


def _channel_utilization(aps: list) -> dict:
    """Mean channel utilization + AP count per channel, grouped by band — used
    for the fleet channel-congestion heatmap."""
    acc: dict = defaultdict(lambda: defaultdict(lambda: {"util_sum": 0.0, "n": 0}))
    for a in aps:
        for r in a.get("radios", []) or []:
            band = r.get("band") or ""
            ch = r.get("channel")
            if not band or ch is None:
                continue
            cell = acc[band][str(ch)]
            cell["util_sum"] += float(r.get("channel_utilization_pct") or 0)
            cell["n"] += 1

    def _chan_key(kv):
        try:
            return (0, int(kv[0]))
        except (TypeError, ValueError):
            return (1, kv[0])

    out: dict = {}
    for band, channels in acc.items():
        out[band] = {
            ch: {"utilization_pct": round(c["util_sum"] / c["n"]) if c["n"] else 0,
                 "ap_count": c["n"]}
            for ch, c in sorted(channels.items(), key=_chan_key)
        }
    return out


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def wireless_summary(request):
    """Fleet summary cards + full AP list + channel-utilization heatmap data."""
    aps = _all_ap_entries()
    data = _summary(aps)
    data["aps"] = aps
    data["channel_utilization"] = _channel_utilization(aps)
    return Response(data)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def wireless_aps(request):
    """Flat list of every wireless AP (UniFi snapshots + Mist inventory rows)."""
    return Response(_all_ap_entries())


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def wireless_location(request):
    """Serve the cached Mist warehouse-dashboard payload (see mist_location.py).
    The scheduler keeps the cache warm; this view just returns it, with an
    on-demand refresh fallback so the first hit after a restart still works.
    Auth-only, like the rest of /api/wireless/ — the browser never sees the
    Mist token.

    Query params:
        site   Mist site id   (required)
        map    Mist map id    (optional; defaults to the site's first map)
    """
    from . import mist_location

    site_id = request.query_params.get("site")
    map_id = request.query_params.get("map")
    if not site_id:
        return Response({"error": "site query param is required"}, status=400)

    payload = mist_location.get_cached(site_id, map_id) if map_id else None
    if payload is None:
        try:
            payload = mist_location.refresh(site_id, map_id)
        except ValueError as exc:
            return Response({"error": str(exc)}, status=404)
        except Exception:  # noqa: BLE001
            return Response({"error": "Mist location refresh failed"}, status=502)
    if payload is None:
        return Response({"error": "Mist integration not configured"}, status=409)
    return Response(payload)
