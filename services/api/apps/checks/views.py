import asyncio

from django.utils import timezone
from django_filters import rest_framework as df
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.mixins import ListModelMixin, RetrieveModelMixin
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet

from .models import CheckResult, ServiceCheck
from .runner import run_check
from .serializers import CheckResultSerializer, ServiceCheckSerializer
from .service import check_to_dict, persist_result

# Map ?period= to a timedelta for result history.
_PERIODS = {"1h": 1, "6h": 6, "24h": 24, "7d": 168}


class ServiceCheckViewSet(viewsets.ModelViewSet):
    """
    Agentless service checks (HTTP/HTTPS, TCP, … externally probed).

    Filter by `check_type`, `current_status`, `device`, `site`; search by name
    or host. `run-now/` probes immediately; `results/` returns recent history;
    `summary/` returns up/down/degraded counts.
    """

    queryset = ServiceCheck.objects.select_related("device", "site").all()
    serializer_class = ServiceCheckSerializer
    filterset_fields = ["check_type", "current_status", "device", "site", "is_active", "is_enabled"]
    search_fields = ["name", "host"]
    ordering_fields = ["name", "check_type", "current_status", "last_checked", "created_at"]
    ordering = ["name"]

    @action(detail=True, methods=["post"], url_path="run-now")
    def run_now(self, request, pk=None):
        """Probe this check immediately, record the result and return it."""
        check = self.get_object()
        result = asyncio.run(run_check(check_to_dict(check)))
        now = timezone.now()
        persist_result(check, result, now)
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
        """Recent CheckResults for this check within ?period=1h|6h|24h|7d."""
        check = self.get_object()
        period = request.query_params.get("period", "24h")
        hours = _PERIODS.get(period, 24)
        since = timezone.now() - timezone.timedelta(hours=hours)
        qs = list(CheckResult.objects.filter(service_check=check, checked_at__gte=since).order_by("-checked_at")[:500])
        return Response({
            "period": period,
            "count": len(qs),
            "results": CheckResultSerializer(qs, many=True).data,
        })

    @action(detail=False, methods=["get"])
    def summary(self, request):
        """Counts of active checks by current status."""
        qs = ServiceCheck.objects.filter(is_active=True)
        counts = {"up": 0, "down": 0, "degraded": 0, "unknown": 0}
        for row in qs.values("current_status"):
            counts[row["current_status"]] = counts.get(row["current_status"], 0) + 1
        counts["total"] = sum(counts.values())
        return Response(counts)


class CheckResultFilter(df.FilterSet):
    # Public filter key stays "check" though the model attribute is service_check.
    check = df.NumberFilter(field_name="service_check")

    class Meta:
        model = CheckResult
        fields = ["check", "status"]


class CheckResultViewSet(ListModelMixin, RetrieveModelMixin, GenericViewSet):
    """Read-only access to individual probe results."""

    queryset = CheckResult.objects.select_related("service_check").all()
    serializer_class = CheckResultSerializer
    filterset_class = CheckResultFilter
    ordering_fields = ["checked_at"]
    ordering = ["-checked_at"]
