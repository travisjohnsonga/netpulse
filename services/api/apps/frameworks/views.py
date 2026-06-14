"""Regulatory framework API: list, per-framework assessment, PDF evidence package."""
from __future__ import annotations

import logging

from django.http import HttpResponse
from django.utils import timezone
from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from .engine import evaluate_framework, framework_summary
from .models import RegulatoryFramework

logger = logging.getLogger(__name__)


class RegulatoryFrameworkViewSet(viewsets.ViewSet):
    """
    Browse regulatory frameworks and produce auditor evidence.

    - ``GET /api/frameworks/`` — all frameworks with coverage roll-up.
    - ``GET /api/frameworks/{key}/`` — full per-control assessment.
    - ``GET /api/frameworks/{key}/report/`` — PDF evidence package.
    """

    permission_classes = [permissions.IsAuthenticated]
    lookup_field = "key"

    def list(self, request):
        frameworks = RegulatoryFramework.objects.filter(enabled=True).prefetch_related("controls")
        return Response([framework_summary(fw) for fw in frameworks])

    def retrieve(self, request, key=None):
        fw = RegulatoryFramework.objects.filter(key=key).prefetch_related("controls").first()
        if fw is None:
            return Response({"error": "Framework not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response(evaluate_framework(fw))

    @action(detail=True, methods=["get"], url_path="report")
    def report(self, request, key=None):
        """Generate a PDF evidence package for the framework."""
        fw = RegulatoryFramework.objects.filter(key=key).prefetch_related("controls").first()
        if fw is None:
            return Response({"error": "Framework not found."}, status=status.HTTP_404_NOT_FOUND)
        try:
            from .pdf import build_evidence_pdf
        except ImportError:
            return Response(
                {"error": "PDF generation unavailable (reportlab not installed)."},
                status=status.HTTP_501_NOT_IMPLEMENTED)
        report_data = evaluate_framework(fw)
        generated_at = timezone.now().strftime("%Y-%m-%d %H:%M UTC")
        pdf_bytes = build_evidence_pdf(report_data, generated_at=generated_at)
        resp = HttpResponse(pdf_bytes, content_type="application/pdf")
        fname = f"spane-{fw.key}-evidence-{timezone.now():%Y%m%d}.pdf"
        resp["Content-Disposition"] = f'attachment; filename="{fname}"'
        return resp
