import asyncio

from django.utils import timezone
from django_filters import rest_framework as df
from rest_framework import status as http_status
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.mixins import ListModelMixin, RetrieveModelMixin
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet

from apps.core.permissions import CapabilityViewSetMixin

from .models import CheckResult, ServiceCheck, ServiceCheckCollector
from .runner import run_check
from .serializers import (
    CheckResultSerializer, ServiceCheckCollectorSerializer, ServiceCheckSerializer,
)
from .service import check_to_dict, persist_result

# Map ?period= to a timedelta for result history.
_PERIODS = {"1h": 1, "6h": 6, "24h": 24, "7d": 168}


class ServiceCheckViewSet(CapabilityViewSetMixin, viewsets.ModelViewSet):
    """
    Agentless service checks (HTTP/HTTPS, TCP, … externally probed).

    Filter by `check_type`, `current_status`, `device`, `site`; search by name
    or host. `run-now/` probes immediately; `results/` returns recent history;
    `summary/` returns up/down/degraded counts.
    """

    view_capability = "check:view"
    write_capability = "check:manage"

    queryset = ServiceCheck.objects.select_related("device", "site").all()
    serializer_class = ServiceCheckSerializer
    filterset_fields = ["check_type", "current_status", "device", "site", "is_active", "is_enabled"]
    search_fields = ["name", "host", "notes"]
    ordering_fields = ["name", "check_type", "current_status", "last_checked", "created_at"]
    ordering = ["name"]

    @action(detail=True, methods=["post"], url_path="run-now")
    def run_now(self, request, pk=None):
        """Probe this check immediately, record the result and return it."""
        check = self.get_object()
        result = asyncio.run(run_check(check_to_dict(check)))
        now = timezone.now()
        from apps.checks.collectors import engine_collector_for
        persist_result(check, result, now, collector=engine_collector_for(check))
        return Response({
            "status": result["status"],
            "response_time_ms": result.get("response_time_ms"),
            "error": result.get("error") or "",
            "details": result.get("details") or {},
            "current_status": check.current_status,
            "checked_at": now,
        })

    @action(detail=True, methods=["get"])
    def results(self, request, pk=None):
        """
        Result history for this check within ?period=1h|6h|24h|7d (default 24h),
        with an uptime summary. Newest first; capped at 500 points.
        """
        check = self.get_object()
        period = request.query_params.get("period", "24h")
        hours = _PERIODS.get(period, 24)
        since = timezone.now() - timezone.timedelta(hours=hours)
        results = CheckResult.objects.filter(service_check=check, checked_at__gte=since)
        # Optional per-collector filter for the multi-location result view.
        collector_id = request.query_params.get("collector_id")
        if collector_id:
            results = results.filter(collector_id=collector_id)
        qs = list(results.select_related("collector").order_by("-checked_at")[:500])

        counts = {"up": 0, "down": 0, "degraded": 0}
        for r in qs:
            counts[r.status] = counts.get(r.status, 0) + 1
        total = len(qs)
        uptime_pct = round(counts["up"] / total * 100, 1) if total else None
        return Response({
            "check_id": check.id,
            "check_name": check.name,
            "period": period,
            "summary": {"total": total, **counts, "uptime_pct": uptime_pct},
            # Aggregate status + per-collector breakdown for the multi-location view.
            "aggregate_status": check.current_status,
            "collector_results": ServiceCheckCollectorSerializer(
                check.collector_assignments.select_related("collector"), many=True).data,
            "results": CheckResultSerializer(qs, many=True).data,
        })

    @action(detail=True, methods=["get", "post"])
    def collectors(self, request, pk=None):
        """GET: list this check's collector assignments.
        POST {collector_id, enabled?}: assign a collector to this check."""
        check = self.get_object()
        if request.method == "GET":
            rows = check.collector_assignments.select_related("collector")
            return Response(ServiceCheckCollectorSerializer(rows, many=True).data)
        collector_id = request.data.get("collector_id")
        if not collector_id:
            return Response({"error": "collector_id is required"}, status=http_status.HTTP_400_BAD_REQUEST)
        from apps.collectors.models import Collector
        if not Collector.objects.filter(pk=collector_id).exists():
            return Response({"error": "collector not found"}, status=http_status.HTTP_400_BAD_REQUEST)
        row, _ = ServiceCheckCollector.objects.update_or_create(
            service_check=check, collector_id=collector_id,
            defaults={"enabled": bool(request.data.get("enabled", True))},
        )
        return Response(ServiceCheckCollectorSerializer(row).data, status=http_status.HTTP_201_CREATED)

    @action(detail=True, methods=["delete"], url_path=r"collectors/(?P<collector_pk>[^/.]+)")
    def remove_collector(self, request, pk=None, collector_pk=None):
        """Unassign a collector from this check."""
        check = self.get_object()
        deleted, _ = ServiceCheckCollector.objects.filter(
            service_check=check, collector_id=collector_pk).delete()
        if not deleted:
            return Response({"error": "assignment not found"}, status=http_status.HTTP_404_NOT_FOUND)
        return Response(status=http_status.HTTP_204_NO_CONTENT)

    @action(detail=False, methods=["get"])
    def summary(self, request):
        """Counts of active checks by current status, plus a per-collector
        breakdown for the multi-location view."""
        qs = ServiceCheck.objects.filter(is_active=True)
        counts = {"up": 0, "down": 0, "degraded": 0, "unknown": 0}
        for row in qs.values("current_status"):
            counts[row["current_status"]] = counts.get(row["current_status"], 0) + 1
        counts["total"] = sum(counts.values())

        # Per-collector pass/fail tallies across all enabled assignments.
        by_collector: dict[int, dict] = {}
        rows = (
            ServiceCheckCollector.objects.filter(enabled=True)
            .select_related("collector")
            .values("collector_id", "collector__name", "last_result")
        )
        for r in rows:
            entry = by_collector.setdefault(r["collector_id"], {
                "collector_id": r["collector_id"], "collector_name": r["collector__name"],
                "passing": 0, "failing": 0, "unknown": 0,
            })
            entry[r["last_result"]] = entry.get(r["last_result"], 0) + 1
        counts["by_collector"] = list(by_collector.values())
        return Response(counts)


class CheckResultFilter(df.FilterSet):
    # Public filter key stays "check" though the model attribute is service_check.
    check = df.NumberFilter(field_name="service_check")

    class Meta:
        model = CheckResult
        fields = ["check", "status"]


class CheckResultViewSet(CapabilityViewSetMixin, ListModelMixin, RetrieveModelMixin, GenericViewSet):
    """Read-only access to individual probe results."""

    view_capability = "check:view"

    queryset = CheckResult.objects.select_related("service_check").all()
    serializer_class = CheckResultSerializer
    filterset_class = CheckResultFilter
    ordering_fields = ["checked_at"]
    ordering = ["-checked_at"]
