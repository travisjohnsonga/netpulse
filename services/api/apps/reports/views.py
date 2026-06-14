"""Reports API: on-demand generation, schedules, history, download."""
from __future__ import annotations

import logging
import os

from django.conf import settings
from django.http import FileResponse
from rest_framework import mixins, permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from .generate import generate
from .models import GeneratedReport, ReportSchedule, ReportType
from .serializers import (
    ComplianceSummaryRequestSerializer,
    DailyOpsRequestSerializer,
    GeneratedReportSerializer,
    ReportScheduleSerializer,
)
from .storage import content_type, download_filename

logger = logging.getLogger(__name__)


def _generate_and_respond(report_type, fmt, params, request):
    """Generate a report and return it inline (JSON body or file download)."""
    try:
        report, content, _data = generate(report_type, fmt, params, user=request.user, source="on-demand")
    except ValueError as exc:
        return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
    if fmt == "json":
        import json
        return Response(json.loads(content))
    resp = FileResponse(open(os.path.join(settings.MEDIA_ROOT, report.file_path), "rb"),
                        content_type=content_type(fmt))
    resp["Content-Disposition"] = f'attachment; filename="{download_filename(report)}"'
    return resp


class ComplianceSummaryView(APIView):
    permission_classes = [permissions.IsAuthenticated]

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
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        req = DailyOpsRequestSerializer(data=request.data)
        req.is_valid(raise_exception=True)
        d = req.validated_data
        params = {
            "date": d["date"].isoformat() if d.get("date") else None,
            "site_ids": d.get("site_ids") or [],
        }
        return _generate_and_respond(ReportType.DAILY_OPS, d["format"], params, request)


class ScheduleListCreateView(APIView):
    """GET (list) / POST (create) schedules for one report type (spec endpoint)."""
    permission_classes = [permissions.IsAuthenticated]
    report_type = None  # set per-URL

    def get(self, request):
        qs = ReportSchedule.objects.filter(report_type=self.report_type)
        return Response(ReportScheduleSerializer(qs, many=True).data)

    def post(self, request):
        data = {**request.data, "report_type": self.report_type}
        ser = ReportScheduleSerializer(data=data)
        ser.is_valid(raise_exception=True)
        ser.save()
        return Response(ser.data, status=status.HTTP_201_CREATED)


class ComplianceScheduleView(ScheduleListCreateView):
    report_type = ReportType.COMPLIANCE_SUMMARY


class DailyOpsScheduleView(ScheduleListCreateView):
    report_type = ReportType.DAILY_OPS


class ReportScheduleViewSet(viewsets.ModelViewSet):
    """Manage existing schedules (update/delete/list-all)."""
    queryset = ReportSchedule.objects.all()
    serializer_class = ReportScheduleSerializer
    permission_classes = [permissions.IsAuthenticated]


class GeneratedReportViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin,
                             viewsets.GenericViewSet):
    """Report history + download."""
    queryset = GeneratedReport.objects.select_related("generated_by").all()
    serializer_class = GeneratedReportSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        qs = super().get_queryset()
        rt = self.request.query_params.get("report_type")
        return qs.filter(report_type=rt) if rt else qs

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
