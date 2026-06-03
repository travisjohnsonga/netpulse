import logging
import threading

from django.db.models import Count
from rest_framework import generics
from rest_framework.decorators import action
from rest_framework.mixins import ListModelMixin, RetrieveModelMixin, UpdateModelMixin
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet

from apps.core.permissions import AdminOnly

from .models import CVE, CVEFeedSettings, DeviceCVE
from .serializers import CVEFeedSettingsSerializer, CVESerializer, DeviceCVESerializer

logger = logging.getLogger(__name__)


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
        return super().get_queryset().annotate(
            affected_device_count=Count("affected_devices", distinct=True),
        )

    @action(detail=False, methods=["get"])
    def summary(self, request):
        """Headline stats for the CVE dashboard."""
        by_sev = dict(
            CVE.objects.values_list("severity").annotate(n=Count("id")).values_list("severity", "n"),
        )
        affected = (
            DeviceCVE.objects.filter(is_patched=False).values("device").distinct().count()
        )
        settings_obj = CVEFeedSettings.load()
        return Response({
            "total": CVE.objects.count(),
            "by_severity": by_sev,
            "critical": by_sev.get("critical", 0),
            "high": by_sev.get("high", 0),
            "medium": by_sev.get("medium", 0),
            "low": by_sev.get("low", 0),
            "kev_count": CVE.objects.filter(cisa_kev=True).count(),
            "affected_devices": affected,
            "patched": DeviceCVE.objects.filter(is_patched=True).count(),
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
