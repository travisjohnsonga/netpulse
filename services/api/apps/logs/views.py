"""OpenSearch-backed fleet log query API.

GET /api/logs/ queries the ``netpulse-logs-*`` indices written by the
stream-processor (which consumes ``netpulse.logs.>`` off NATS) and returns a
paginated, severity-summarised result set.

The OpenSearch call is isolated in :func:`_execute` so tests can monkeypatch it,
and the view degrades gracefully (empty result, no error) when the store is
unavailable.
"""
from __future__ import annotations

import logging

from django.conf import settings
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.errors import safe_detail
from apps.core.permissions import CapabilityViewSetMixin, HasCapability
from .models import LogFilter
from .serializers import LogFilterSerializer

logger = logging.getLogger(__name__)

LOG_INDEX = "netpulse-logs-*"

# Canonical syslog severities (highest → lowest) mapped to the keyword variants
# that turn up in the wild. Short syslog tokens (err/crit/warn) and the long
# forms (error/critical/warning) must both filter and aggregate as one severity.
SEVERITY_SYNONYMS: dict[str, list[str]] = {
    "emergency": ["emerg", "emergency", "panic"],
    "alert": ["alert"],
    "critical": ["crit", "critical"],
    "error": ["err", "error"],
    "warning": ["warn", "warning"],
    "notice": ["notice"],
    "info": ["info", "informational"],
    "debug": ["debug"],
}

# Reverse lookup: any variant (or the canonical name itself) → canonical name.
_SEVERITY_CANONICAL: dict[str, str] = {
    variant: canonical
    for canonical, variants in SEVERITY_SYNONYMS.items()
    for variant in (*variants, canonical)
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
    """Run a search against the log indices. Isolated for testing."""
    return _client().search(index=LOG_INDEX, body=body)


def _canonical_severity(value: str) -> str:
    return _SEVERITY_CANONICAL.get(value.strip().lower(), value.strip().lower())


def _expand_severities(raw: str) -> list[str]:
    """Expand a comma-separated severity filter to all matching keyword variants."""
    variants: set[str] = set()
    for token in raw.split(","):
        token = token.strip().lower()
        if not token:
            continue
        canonical = _SEVERITY_CANONICAL.get(token, token)
        variants.update(SEVERITY_SYNONYMS.get(canonical, [token]))
    return sorted(variants)


def _device_identifiers(devices) -> list[str]:
    """Sorted, de-duplicated hostname + IP values for a set of devices."""
    ids: set[str] = set()
    for dev in devices:
        if dev.hostname:
            ids.add(str(dev.hostname))
        if dev.ip_address:
            ids.add(str(dev.ip_address))
    return sorted(ids)


def _device_should_clause(ids: list[str]) -> dict:
    """Match a log doc whose hostname OR source_ip is one of the device's identifiers."""
    return {
        "bool": {
            "should": [
                {"terms": {"hostname.keyword": ids}},
                {"terms": {"source_ip.keyword": ids}},
            ],
            "minimum_should_match": 1,
        }
    }


def _empty_summary() -> dict[str, int]:
    return {canonical: 0 for canonical in SEVERITY_SYNONYMS}


def _apply_filters_enabled(params) -> bool:
    """Whether suppress filters apply (default true; ?apply_filters=false to skip)."""
    return str(params.get("apply_filters", "true")).lower() not in ("false", "0", "no")


def _platform_by_hostname(results: list[dict]) -> dict[str, str]:
    """Map each page hostname → device platform (one query) for platform-scoped filters."""
    hostnames = {r.get("hostname") for r in results if r.get("hostname")}
    if not hostnames:
        return {}
    from apps.devices.models import Device

    return {
        hn: plat
        for hn, plat in Device.objects.filter(hostname__in=hostnames).values_list(
            "hostname", "platform"
        )
    }


def _apply_suppress_filters(results: list[dict]) -> tuple[list[dict], int]:
    """
    Drop log rows matched by any enabled SUPPRESS filter (regex on the message,
    optionally scoped to the device's platform). Returns (kept_rows, dropped_count).
    Best-effort: a DB error leaves results untouched.
    """
    try:
        filters = list(
            LogFilter.objects.filter(enabled=True, action=LogFilter.Action.SUPPRESS)
        )
    except Exception as exc:  # noqa: BLE001 — never break the log view on filter errors
        logger.warning("Could not load log filters: %s", exc)
        return results, 0
    if not filters:
        return results, 0

    platform_by_host = _platform_by_hostname(results)
    kept, suppressed = [], 0
    for row in results:
        message = row.get("message") or ""
        platform = platform_by_host.get(row.get("hostname"))
        drop = any(
            (not f.platforms or platform in f.platforms) and f.test(message)
            for f in filters
        )
        if drop:
            suppressed += 1
        else:
            kept.append(row)
    return kept, suppressed


class LogQueryView(APIView):
    permission_classes = [HasCapability("log:view")]

    def get(self, request):
        from apps.devices.models import Device

        params = request.query_params
        musts: list[dict] = []

        # ── Device scoping ──────────────────────────────────────────────────
        hostname = params.get("device_hostname")
        if hostname:
            device = Device.objects.filter(hostname=hostname).first()
            if device:
                musts.append(_device_should_clause(_device_identifiers([device])))
            else:
                musts.append({"term": {"hostname.keyword": hostname}})

        device_id = params.get("device_id")
        if device_id:
            device = Device.objects.filter(pk=device_id).first()
            if device:
                musts.append(_device_should_clause(_device_identifiers([device])))

        site = params.get("site")
        if site:
            ids = _device_identifiers(Device.objects.filter(site_id=site))
            if ids:
                musts.append(_device_should_clause(ids))

        # ── Severity ────────────────────────────────────────────────────────
        severity = params.get("severity")
        if severity:
            variants = _expand_severities(severity)
            if variants:
                musts.append({"terms": {"severity_name.keyword": variants}})

        # ── Free-text search ────────────────────────────────────────────────
        search = params.get("search")
        if search:
            musts.append({"match": {"message": search}})

        # ── Time window (syslog `timestamp` is often null → use @timestamp) ──
        time_range: dict[str, str] = {}
        if params.get("from"):
            time_range["gte"] = params["from"]
        if params.get("to"):
            time_range["lte"] = params["to"]
        if time_range:
            musts.append({"range": {"@timestamp": time_range}})

        # ── Pagination ──────────────────────────────────────────────────────
        try:
            page = max(1, int(params.get("page", 1)))
        except (TypeError, ValueError):
            page = 1
        try:
            page_size = max(1, min(500, int(params.get("page_size", 50))))
        except (TypeError, ValueError):
            page_size = 50

        body = {
            "query": {"bool": {"must": musts}},
            "sort": [{"@timestamp": {"order": "desc"}}],
            "from": (page - 1) * page_size,
            "size": page_size,
            "aggs": {
                "by_severity": {
                    "terms": {"field": "severity_name.keyword", "size": 50}
                }
            },
        }

        try:
            raw = _execute(body)
        except Exception as exc:  # store down / connection refused → degrade
            logger.warning("Log query failed, returning empty result: %s", exc)
            return Response(
                {"count": 0, "results": [], "summary": {"by_severity": _empty_summary()}}
            )

        formatted = self._format(raw)

        # Apply enabled suppress filters to the page unless explicitly disabled.
        suppressed = 0
        if _apply_filters_enabled(params):
            formatted["results"], suppressed = _apply_suppress_filters(formatted["results"])

        resp = Response(formatted)
        resp["X-Suppressed-Count"] = str(suppressed)
        return resp

    @staticmethod
    def _format(raw: dict) -> dict:
        hits = raw.get("hits", {})
        total = hits.get("total", 0)
        count = total.get("value", 0) if isinstance(total, dict) else total

        results = []
        for hit in hits.get("hits", []):
            src = hit.get("_source", {})
            sev = src.get("severity_name") or ""
            results.append(
                {
                    "id": hit.get("_id"),
                    "timestamp": src.get("timestamp") or src.get("@timestamp"),
                    "hostname": src.get("hostname"),
                    "severity": sev,
                    "severity_label": sev.title(),
                    "facility": src.get("facility_name"),
                    "program": src.get("app_name"),
                    "proc_id": src.get("proc_id"),
                    "message": src.get("message"),
                    "source_ip": src.get("source_ip"),
                    "raw": src.get("raw"),
                }
            )

        summary = _empty_summary()
        buckets = (
            raw.get("aggregations", {}).get("by_severity", {}).get("buckets", [])
        )
        for bucket in buckets:
            canonical = _canonical_severity(str(bucket.get("key", "")))
            summary[canonical] = summary.get(canonical, 0) + bucket.get("doc_count", 0)

        return {"count": count, "results": results, "summary": {"by_severity": summary}}


class LogFilterViewSet(CapabilityViewSetMixin, viewsets.ModelViewSet):
    """
    Manage log filters — regex rules that suppress noise, highlight, or tag fleet
    log messages, optionally scoped to specific platforms.

    Suppress filters are applied to `GET /api/logs/` results (disable per-request
    with `?apply_filters=false`). The `test/` action dry-runs a pattern against a
    sample message.
    """

    view_capability = "log:view"
    write_capability = "log:edit"

    queryset = LogFilter.objects.all()
    serializer_class = LogFilterSerializer
    filterset_fields = ["action", "enabled"]
    search_fields = ["name", "pattern", "tag"]
    ordering_fields = ["name", "created_at"]
    ordering = ["name"]

    def perform_create(self, serializer):
        user = self.request.user if self.request.user.is_authenticated else None
        serializer.save(created_by=user)

    @action(detail=False, methods=["post"])
    def test(self, request):
        """Dry-run a pattern against a message → {matches, error}. Invalid regex
        returns matches=false with the error string (never 400)."""
        import re

        pattern = request.data.get("pattern", "")
        message = request.data.get("message", "")
        try:
            matches = bool(re.search(pattern, message or "", re.IGNORECASE))
            return Response({"matches": matches, "error": None})
        except re.error as exc:
            return Response({"matches": False, "error": safe_detail(
                exc, logger, "log-filter regex test", public="Invalid regular expression.")})
