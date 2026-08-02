"""Reports API: on-demand generation, schedules, history, download."""
from __future__ import annotations

import logging
import os

from django.conf import settings
from django.http import FileResponse
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import SAFE_METHODS
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.errors import safe_detail
from apps.core.permissions import CapabilityViewSetMixin, HasCapability

from .generate import generate
from .models import GeneratedReport, ReportSchedule, ReportType
from .serializers import (
    ComplianceSummaryRequestSerializer,
    DailyOpsRequestSerializer,
    GeneratedReportSerializer,
    OpsReportRequestSerializer,
    ReportScheduleSerializer,
)
from .storage import content_type, download_filename

logger = logging.getLogger(__name__)


def _generate_and_respond(report_type, fmt, params, request):
    """Generate a report and return it inline (JSON body or file download)."""
    try:
        report, content, _data = generate(report_type, fmt, params, user=request.user, source="on-demand")
    except ValueError as exc:
        # Don't echo the raw exception text back (CodeQL: information exposure);
        # log the detail server-side and return a safe, static message.
        return Response(
            {"error": safe_detail(exc, logger, "report generation",
                                  public="Invalid report request.")},
            status=status.HTTP_400_BAD_REQUEST)
    if fmt == "json":
        import json
        return Response(json.loads(content))
    resp = FileResponse(open(os.path.join(settings.MEDIA_ROOT, report.file_path), "rb"),
                        content_type=content_type(fmt))
    resp["Content-Disposition"] = f'attachment; filename="{download_filename(report)}"'
    return resp


class ComplianceSummaryView(APIView):
    permission_classes = [HasCapability("report:generate")]

    def post(self, request):
        req = ComplianceSummaryRequestSerializer(data=request.data)
        req.is_valid(raise_exception=True)
        d = req.validated_data
        params = {
            "group_by": d.get("group_by") or ["site", "role", "platform"],
            "site_ids": d.get("site_ids") or [],
            "include_score_breakdown": d.get("include_score_breakdown", True),
            "as_of": d["as_of"].isoformat() if d.get("as_of") else None,
        }
        return _generate_and_respond(ReportType.COMPLIANCE_SUMMARY, d["format"], params, request)


class DailyOpsView(APIView):
    permission_classes = [HasCapability("report:generate")]

    def post(self, request):
        req = DailyOpsRequestSerializer(data=request.data)
        req.is_valid(raise_exception=True)
        d = req.validated_data
        params = {
            "date": d["date"].isoformat() if d.get("date") else None,
            "site_ids": d.get("site_ids") or [],
        }
        return _generate_and_respond(ReportType.DAILY_OPS, d["format"], params, request)


class OpsReportView(APIView):
    """Operations report for any reporting period (daily/weekly/monthly/quarterly)."""
    permission_classes = [HasCapability("report:generate")]

    def post(self, request):
        req = OpsReportRequestSerializer(data=request.data)
        req.is_valid(raise_exception=True)
        d = req.validated_data
        params = {
            "period": d["period"],
            "end_date": d["end_date"].isoformat() if d.get("end_date") else None,
            "site_ids": d.get("site_ids") or [],
        }
        return _generate_and_respond(ReportType.DAILY_OPS, d["format"], params, request)


class ScheduleListCreateView(APIView):
    """GET (list) / POST (create) schedules for one report type (spec endpoint)."""
    report_type = None  # set per-URL

    def get_permissions(self):
        if self.request.method in SAFE_METHODS:
            return [HasCapability("report:view")()]
        return [HasCapability("report:generate")()]

    def get(self, request):
        qs = ReportSchedule.objects.filter(report_type=self.report_type)
        # Pass request context so the serializer can convert UTC→user-local time.
        return Response(ReportScheduleSerializer(qs, many=True, context={"request": request}).data)

    def post(self, request):
        data = {**request.data, "report_type": self.report_type}
        ser = ReportScheduleSerializer(data=data, context={"request": request})
        ser.is_valid(raise_exception=True)
        ser.save()
        return Response(ser.data, status=status.HTTP_201_CREATED)


class ComplianceScheduleView(ScheduleListCreateView):
    report_type = ReportType.COMPLIANCE_SUMMARY


class DailyOpsScheduleView(ScheduleListCreateView):
    report_type = ReportType.DAILY_OPS


class ReportScheduleViewSet(CapabilityViewSetMixin, viewsets.ModelViewSet):
    """Manage existing schedules (update/delete/list-all)."""
    view_capability = "report:view"
    write_capability = "report:generate"
    queryset = ReportSchedule.objects.all()
    serializer_class = ReportScheduleSerializer


def _delete_report_file(report) -> None:
    """Best-effort removal of a stored report's file from disk."""
    if not report.file_path:
        return
    abs_path = os.path.join(settings.MEDIA_ROOT, report.file_path)
    try:
        if os.path.exists(abs_path):
            os.remove(abs_path)
    except OSError as exc:  # noqa: BLE001 — DB row removal must still proceed
        logger.warning("could not delete report file %s: %s", report.file_path, exc)


class GeneratedReportViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin,
                             mixins.DestroyModelMixin, viewsets.GenericViewSet):
    """Report history + download + delete (single and bulk)."""
    queryset = GeneratedReport.objects.select_related("generated_by").all()
    serializer_class = GeneratedReportSerializer

    def get_permissions(self):
        if self.action in ("list", "retrieve", "download"):
            return [HasCapability("report:view")()]
        return [HasCapability("report:generate")()]

    def get_queryset(self):
        qs = super().get_queryset()
        rt = self.request.query_params.get("report_type")
        return qs.filter(report_type=rt) if rt else qs

    def perform_destroy(self, instance):
        """DELETE /api/reports/{id}/ — remove the stored file then the row."""
        _delete_report_file(instance)
        instance.delete()

    @action(detail=False, methods=["post"], url_path="bulk-delete")
    def bulk_delete(self, request):
        """POST /api/reports/bulk-delete/ {"ids": [...]} — delete many reports."""
        ids = request.data.get("ids")
        if not isinstance(ids, list) or not ids:
            return Response({"error": "Provide a non-empty 'ids' list."},
                            status=status.HTTP_400_BAD_REQUEST)
        reports = list(GeneratedReport.objects.filter(pk__in=ids))
        for report in reports:
            _delete_report_file(report)
        deleted = len(reports)
        GeneratedReport.objects.filter(pk__in=[r.pk for r in reports]).delete()
        return Response({"deleted": deleted})

    @action(detail=True, methods=["get"], url_path="download")
    def download(self, request, pk=None):
        report = self.get_object()
        abs_path = os.path.join(settings.MEDIA_ROOT, report.file_path)
        if not report.file_path or not os.path.exists(abs_path):
            return Response({"error": "Report file is no longer available."},
                            status=status.HTTP_404_NOT_FOUND)
        resp = FileResponse(open(abs_path, "rb"), content_type=content_type(report.format))
        resp["Content-Disposition"] = f'attachment; filename="{download_filename(report)}"'
        return resp
