import logging
import threading

from django.db.models import Count, Q
from rest_framework import generics
from rest_framework.decorators import action
from rest_framework.mixins import ListModelMixin, RetrieveModelMixin, UpdateModelMixin
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet

from apps.core.permissions import AdminOnly

from .models import CVE, CVEFeedSettings, DeviceCVE
from .serializers import CVEFeedSettingsSerializer, CVESerializer, DeviceCVESerializer

logger = logging.getLogger(__name__)


def inventory_platforms() -> list[str]:
    """Distinct platforms of active devices — the inventory's CVE relevance set."""
    from apps.devices.models import Device
    return list(
        Device.objects.filter(status=Device.Status.ACTIVE)
        .exclude(platform="")
        .values_list("platform", flat=True)
        .distinct()
    )


def platforms_q(platforms: list[str]) -> Q:
    """
    Match CVEs whose ``affected_platforms`` JSON list contains any of these
    platform keys. Uses a quoted icontains so it works on both PostgreSQL and
    SQLite (the JSON ``contains``/``has_any_keys`` lookups are Postgres-only).
    The surrounding quotes keep it exact ("ios" never matches "ios_xe").
    """
    q = Q()
    for p in platforms:
        q |= Q(affected_platforms__icontains=f'"{p}"')
    return q


def _inventory_relevance(platforms: list[str]) -> Q:
    """
    A CVE is relevant to the inventory when it affects an inventory platform OR
    is already linked to a device (covers community advisories, whose CVE rows
    carry device links but no affected_platforms). Note platforms_q([]) is an
    empty Q that matches everything, so it must not be OR'd in when empty.
    """
    relevance = Q(affected_devices__isnull=False)
    if platforms:
        relevance |= platforms_q(platforms)
    return relevance


class CVEViewSet(ListModelMixin, RetrieveModelMixin, GenericViewSet):
    """
    Browse the CVE intelligence catalog.

    Read-only access to CVEs ingested from feeds (NVD, Cisco PSIRT, community).
    Filter by `severity`, `source`, `cisa_kev`; search by CVE ID or description;
    order by CVSS score / publish date. Per-device exposure lives at
    `/api/cve/device-cves/`.
    """

    queryset = CVE.objects.all()
    serializer_class = CVESerializer
    filterset_fields = ["severity", "source", "cisa_kev"]
    search_fields = ["cve_id", "description"]
    ordering_fields = ["cvss_score", "published_at", "severity"]
    ordering = ["-cvss_score"]

    def get_queryset(self):
        qs = super().get_queryset().annotate(
            affected_device_count=Count("affected_devices", distinct=True),
        )
        # ?platform=ios_xe pins to one platform; otherwise inventory_only=true
        # (the default) limits to platforms present in the active inventory so
        # the feed isn't cluttered with CVEs for gear we don't run.
        platform = self.request.query_params.get("platform")
        if platform:
            return qs.filter(platforms_q([platform]))
        inventory_only = self.request.query_params.get("inventory_only", "true").lower() != "false"
        if inventory_only:
            plats = inventory_platforms()
            # With no active devices there is nothing to scope to — show all
            # rather than an empty feed.
            if plats:
                qs = qs.filter(_inventory_relevance(plats)).distinct()
        return qs

    @action(detail=False, methods=["get"])
    def summary(self, request):
        """
        Headline stats for the CVE dashboard. Scoped to inventory platforms by
        default (inventory_only=false for the whole catalog). Includes the
        inventory platform list so the UI can build its platform filter.
        """
        plats = inventory_platforms()
        inventory_only = request.query_params.get("inventory_only", "true").lower() != "false"
        qs = CVE.objects.all()
        if inventory_only and plats:
            qs = qs.filter(_inventory_relevance(plats)).distinct()

        by_sev = dict(
            qs.values_list("severity").annotate(n=Count("id")).values_list("severity", "n"),
        )
        affected = (
            DeviceCVE.objects.filter(is_patched=False).values("device").distinct().count()
        )
        settings_obj = CVEFeedSettings.load()
        return Response({
            "total": qs.count(),
            "by_severity": by_sev,
            "critical": by_sev.get("critical", 0),
            "high": by_sev.get("high", 0),
            "medium": by_sev.get("medium", 0),
            "low": by_sev.get("low", 0),
            "kev_count": qs.filter(cisa_kev=True).count(),
            "affected_devices": affected,
            "patched": DeviceCVE.objects.filter(is_patched=True).count(),
            "inventory_platforms": sorted(plats),
            "last_synced_at": settings_obj.last_synced_at,
            "last_sync_status": settings_obj.last_sync_status,
            "last_sync_summary": settings_obj.last_sync_summary,
        })

    @action(detail=False, methods=["post"], permission_classes=[AdminOnly])
    def sync(self, request):
        """Trigger a CVE sync in the background (admin only)."""
        settings_obj = CVEFeedSettings.load()
        if settings_obj.last_sync_status == "running":
            return Response({"status": "running", "detail": "A sync is already in progress."}, status=409)

        def _run():
            from apps.cve import sync as sync_mod
            try:
                sync_mod.run_sync()
            except Exception:
                logger.exception("background CVE sync failed")

        threading.Thread(target=_run, name="cve-sync", daemon=True).start()
        return Response({"status": "started"}, status=202)


class DeviceCVEViewSet(ListModelMixin, RetrieveModelMixin, UpdateModelMixin, GenericViewSet):
    """
    Per-device CVE exposure. List/retrieve, plus PATCH to mark a CVE
    patched/un-patched on a device (`{"is_patched": true}`).
    """

    queryset = DeviceCVE.objects.select_related("device", "cve").all()
    serializer_class = DeviceCVESerializer
    filterset_fields = ["device", "is_patched", "cve__severity", "cve__cisa_kev", "match_type"]


class CVEFeedSettingsView(generics.RetrieveUpdateAPIView):
    """Get or update the CVE feed settings (enable toggles + credentials)."""

    serializer_class = CVEFeedSettingsSerializer

    def get_object(self):
        return CVEFeedSettings.load()
