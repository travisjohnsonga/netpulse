"""OpenSearch-backed flow (NetFlow / sFlow / IPFIX) analytics API.

The flow records are written by the stream-processor to the monthly
``netpulse-flows-YYYY.MM`` indices (consumed off ``netpulse.flows.>`` on NATS).
Each doc carries: exporter_ip, protocol_version, @timestamp, src_ip, dst_ip,
src_port, dst_port, ip_protocol, bytes, packets, duration_ms, input_if,
output_if, tcp_flags, tos.

Endpoints (all read-only, IsAuthenticated):
  GET /api/flows/                recent flows (filter by device/src/dst/proto/window)
  GET /api/flows/top-talkers/    top source IPs by bytes / packets / flows
  GET /api/flows/summary/        totals, unique IPs, protocol mix, bytes-over-time
  GET /api/flows/search/         all flows where an IP is src OR dst

The OpenSearch call is isolated in :func:`_execute` so tests can monkeypatch it,
and every view degrades gracefully (empty result, HTTP 200) when the store is
unavailable.
"""
from __future__ import annotations

import logging

from django.conf import settings
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .protocols import PROTOCOL_NUMBERS, protocol_name, service_name

logger = logging.getLogger(__name__)

FLOW_INDEX = "netpulse-flows-*"

# String fields land in OpenSearch under dynamic mapping as text + a `.keyword`
# sub-field; term filters and terms aggregations must target the keyword.
_IP_KW = {
    "exporter_ip": "exporter_ip.keyword",
    "src_ip": "src_ip.keyword",
    "dst_ip": "dst_ip.keyword",
}

# ?window= → OpenSearch date-math lower bound. Anything else falls back to 1h.
WINDOWS: dict[str, str] = {
    "15m": "now-15m",
    "1h": "now-1h",
    "6h": "now-6h",
    "12h": "now-12h",
    "24h": "now-24h",
    "7d": "now-7d",
}

# date_histogram bucket size per window (keeps each chart ~12–48 points).
HIST_INTERVAL: dict[str, str] = {
    "15m": "1m",
    "1h": "5m",
    "6h": "30m",
    "12h": "1h",
    "24h": "1h",
    "7d": "6h",
}


def _client():
    """Build a synchronous OpenSearch client from settings."""
    from opensearchpy import OpenSearch

    auth = (
        (settings.OPENSEARCH_USER, settings.OPENSEARCH_PASSWORD)
        if settings.OPENSEARCH_PASSWORD
        else None
    )
    return OpenSearch(
        hosts=[{"host": settings.OPENSEARCH_HOST, "port": settings.OPENSEARCH_PORT}],
        http_auth=auth,
        use_ssl=settings.OPENSEARCH_USE_SSL,
        verify_certs=False,
        ssl_show_warn=False,
    )


def _execute(body: dict) -> dict:
    """Run a search against the flow indices. Isolated for testing."""
    return _client().search(index=FLOW_INDEX, body=body)


def _window(params) -> str:
    """Normalise the ?window= param to a known key (default 1h)."""
    w = str(params.get("window", "1h")).lower()
    return w if w in WINDOWS else "1h"


def _range_filter(window: str) -> dict:
    return {"range": {"@timestamp": {"gte": WINDOWS[window]}}}


def _limit(params, default: int = 100, cap: int = 1000) -> int:
    try:
        return max(1, min(cap, int(params.get("limit", default))))
    except (TypeError, ValueError):
        return default


def _device_exporter_ip(device_id) -> str | None:
    """Resolve a device id → the exporter IP it sends flows from (management_ip
    preferred, else ip_address). Returns None for an unknown device."""
    from apps.devices.models import Device

    dev = Device.objects.filter(pk=device_id).first()
    if not dev:
        return None
    return str(dev.management_ip or dev.ip_address)


def _format_flow(hit: dict) -> dict:
    """Shape one OpenSearch hit into a flow row for the UI."""
    src = hit.get("_source", {})
    proto = src.get("ip_protocol")
    return {
        "id": hit.get("_id"),
        "timestamp": src.get("@timestamp") or src.get("timestamp"),
        "exporter_ip": src.get("exporter_ip"),
        "protocol_version": src.get("protocol_version"),
        "src_ip": src.get("src_ip"),
        "dst_ip": src.get("dst_ip"),
        "src_port": src.get("src_port"),
        "dst_port": src.get("dst_port"),
        "ip_protocol": proto,
        "protocol": protocol_name(proto),
        "service": service_name(src.get("dst_port")) or service_name(src.get("src_port")),
        "bytes": src.get("bytes", 0),
        "packets": src.get("packets", 0),
        "duration_ms": src.get("duration_ms"),
        "input_if": src.get("input_if"),
        "output_if": src.get("output_if"),
        "tcp_flags": src.get("tcp_flags"),
        "tos": src.get("tos"),
    }


def _total(hits: dict) -> int:
    total = hits.get("total", 0)
    return total.get("value", 0) if isinstance(total, dict) else total


class _FlowListBase(APIView):
    """Shared base for the two endpoints that return a recent-flows list."""

    permission_classes = [IsAuthenticated]

    def _run_list(self, musts: list[dict], window: str, limit: int) -> Response:
        musts = [*musts, _range_filter(window)]
        body = {
            "query": {"bool": {"must": musts}},
            "sort": [{"@timestamp": {"order": "desc"}}],
            "size": limit,
            "track_total_hits": True,
        }
        try:
            raw = _execute(body)
        except Exception as exc:  # store down / connection refused → degrade
            logger.warning("Flow query failed, returning empty result: %s", exc)
            return Response({"count": 0, "results": []})

        hits = raw.get("hits", {})
        results = [_format_flow(h) for h in hits.get("hits", [])]
        return Response({"count": _total(hits), "results": results})


class FlowQueryView(_FlowListBase):
    """GET /api/flows/ — recent flows, newest first.

    Filters: ?device_id ?src_ip ?dst_ip ?protocol=tcp|udp|icmp
    ?window=1h|6h|24h|7d (default 1h) ?limit=100 (max 1000).
    """

    def get(self, request):
        params = request.query_params
        musts: list[dict] = []

        device_id = params.get("device_id")
        if device_id:
            exporter = _device_exporter_ip(device_id)
            # Unknown device → match nothing rather than returning the whole fleet.
            musts.append({"term": {_IP_KW["exporter_ip"]: exporter or "__none__"}})

        for field in ("src_ip", "dst_ip"):
            value = params.get(field)
            if value:
                musts.append({"term": {_IP_KW[field]: value}})

        protocol = params.get("protocol")
        if protocol:
            num = PROTOCOL_NUMBERS.get(protocol.strip().lower())
            if num is not None:
                musts.append({"term": {"ip_protocol": num}})

        return self._run_list(musts, _window(params), _limit(params))


class FlowSearchView(_FlowListBase):
    """GET /api/flows/search/?ip=x.x.x.x&window=24h — every flow where the IP is
    the source OR the destination. Backs the IP/MAC Lookup page."""

    def get(self, request):
        params = request.query_params
        ip = (params.get("ip") or "").strip()
        if not ip:
            return Response({"count": 0, "results": [], "ip": ""})

        musts = [
            {
                "bool": {
                    "should": [
                        {"term": {_IP_KW["src_ip"]: ip}},
                        {"term": {_IP_KW["dst_ip"]: ip}},
                    ],
                    "minimum_should_match": 1,
                }
            }
        ]
        resp = self._run_list(musts, _window(params), _limit(params))
        resp.data["ip"] = ip
        return resp


class TopTalkersView(APIView):
    """GET /api/flows/top-talkers/ — top source IPs by bytes | packets | flows.

    ?window=1h|6h|24h ?by=bytes|packets|flows (default bytes) ?limit=10.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        params = request.query_params
        window = _window(params)
        by = str(params.get("by", "bytes")).lower()
        if by not in ("bytes", "packets", "flows"):
            by = "bytes"
        limit = _limit(params, default=10, cap=100)

        # flows → order by the bucket's own doc_count; bytes/packets → by sub-sum.
        order = {"_count": "desc"} if by == "flows" else {"total_bytes" if by == "bytes" else "total_packets": "desc"}
        body = {
            "size": 0,
            "query": {"bool": {"must": [_range_filter(window)]}},
            "aggs": {
                "top_src": {
                    "terms": {
                        "field": _IP_KW["src_ip"],
                        "size": limit,
                        "order": order,
                    },
                    "aggs": {
                        "total_bytes": {"sum": {"field": "bytes"}},
                        "total_packets": {"sum": {"field": "packets"}},
                    },
                }
            },
        }
        try:
            raw = _execute(body)
        except Exception as exc:
            logger.warning("Top-talkers query failed, returning empty: %s", exc)
            return Response({"by": by, "window": window, "results": []})

        buckets = raw.get("aggregations", {}).get("top_src", {}).get("buckets", [])
        results = [
            {
                "src_ip": b.get("key"),
                "flows": b.get("doc_count", 0),
                "bytes": int(b.get("total_bytes", {}).get("value") or 0),
                "packets": int(b.get("total_packets", {}).get("value") or 0),
            }
            for b in buckets
        ]
        return Response({"by": by, "window": window, "results": results})


class FlowSummaryView(APIView):
    """GET /api/flows/summary/ — fleet (or per-device) flow overview for the
    summary cards + protocol donut + bytes-over-time chart.

    ?window=1h|6h|24h ?device_id=1.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        params = request.query_params
        window = _window(params)

        musts: list[dict] = [_range_filter(window)]
        device_id = params.get("device_id")
        if device_id:
            exporter = _device_exporter_ip(device_id)
            musts.append({"term": {_IP_KW["exporter_ip"]: exporter or "__none__"}})

        body = {
            "size": 0,
            "track_total_hits": True,
            "query": {"bool": {"must": musts}},
            "aggs": {
                "total_bytes": {"sum": {"field": "bytes"}},
                "total_packets": {"sum": {"field": "packets"}},
                "unique_src": {"cardinality": {"field": _IP_KW["src_ip"]}},
                "unique_dst": {"cardinality": {"field": _IP_KW["dst_ip"]}},
                "protocols": {
                    "terms": {"field": "ip_protocol", "size": 10},
                    "aggs": {"bytes": {"sum": {"field": "bytes"}}},
                },
                "over_time": {
                    "date_histogram": {
                        "field": "@timestamp",
                        "fixed_interval": HIST_INTERVAL[window],
                        "min_doc_count": 0,
                    },
                    "aggs": {"bytes": {"sum": {"field": "bytes"}}},
                },
            },
        }
        try:
            raw = _execute(body)
        except Exception as exc:
            logger.warning("Flow summary query failed, returning empty: %s", exc)
            return Response(self._empty(window))

        return Response(self._format(raw, window))

    @staticmethod
    def _empty(window: str) -> dict:
        return {
            "window": window,
            "total_flows": 0,
            "total_bytes": 0,
            "total_packets": 0,
            "unique_src_ips": 0,
            "unique_dst_ips": 0,
            "top_protocols": [],
            "bytes_over_time": [],
        }

    @staticmethod
    def _format(raw: dict, window: str) -> dict:
        aggs = raw.get("aggregations", {})
        proto_buckets = aggs.get("protocols", {}).get("buckets", [])
        top_protocols = [
            {
                "protocol": protocol_name(b.get("key")),
                "flows": b.get("doc_count", 0),
                "bytes": int(b.get("bytes", {}).get("value") or 0),
            }
            for b in proto_buckets
        ]
        bytes_over_time = [
            {
                "timestamp": b.get("key_as_string"),
                "bytes": int(b.get("bytes", {}).get("value") or 0),
            }
            for b in aggs.get("over_time", {}).get("buckets", [])
        ]
        return {
            "window": window,
            "total_flows": _total(raw.get("hits", {})),
            "total_bytes": int(aggs.get("total_bytes", {}).get("value") or 0),
            "total_packets": int(aggs.get("total_packets", {}).get("value") or 0),
            "unique_src_ips": int(aggs.get("unique_src", {}).get("value") or 0),
            "unique_dst_ips": int(aggs.get("unique_dst", {}).get("value") or 0),
            "top_protocols": top_protocols,
            "bytes_over_time": bytes_over_time,
        }
