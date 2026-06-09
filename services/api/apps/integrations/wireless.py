"""
Wireless (UniFi AP) fleet API — /api/wireless/.

Reads the per-AP snapshots persisted by the UniFi-telemetry scheduler task
(apps.integrations.unifi_telemetry → UnifiApStatus). The rolling time-series
lives in InfluxDB and is served per-device via /api/devices/{id}/unifi-ap/;
these endpoints serve the fleet overview (summary cards, AP table, channel
heatmap) for the Wireless page.
"""
from __future__ import annotations

from collections import defaultdict

from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response


def _ap_queryset():
    from .models import UnifiApStatus
    return (UnifiApStatus.objects
            .select_related("device", "device__site", "controller")
            .order_by("device__hostname"))


def _serialize_aps(qs):
    from .serializers import UnifiApStatusSerializer
    return UnifiApStatusSerializer(qs, many=True).data


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
    aps = _serialize_aps(_ap_queryset())
    data = _summary(aps)
    data["aps"] = aps
    data["channel_utilization"] = _channel_utilization(aps)
    return Response(data)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def wireless_aps(request):
    """Flat list of every AP's latest snapshot."""
    return Response(_serialize_aps(_ap_queryset()))
