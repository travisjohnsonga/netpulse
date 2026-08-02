"""Regulatory framework API: list, per-framework assessment, PDF evidence package."""
from __future__ import annotations

import logging

from django.http import HttpResponse
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.core.errors import internal_error_response
from apps.core.permissions import HasCapability

from .engine import evaluate_framework, framework_summary
from .models import RegulatoryFramework
from .scope import applicable_frameworks, is_framework_applicable

logger = logging.getLogger(__name__)


class RegulatoryFrameworkViewSet(viewsets.ViewSet):
    """
    Browse regulatory frameworks and produce auditor evidence.

    - ``GET /api/frameworks/`` — all *applicable* frameworks with coverage roll-up.
    - ``GET /api/frameworks/{key}/`` — full per-control assessment.
    - ``GET /api/frameworks/{key}/report/`` — PDF evidence package.

    Every action is scoped to the frameworks this environment is subject to (see
    ``apps.frameworks.scope`` / ``APPLICABLE_COMPLIANCE_FRAMEWORKS``). Out-of-scope
    frameworks are absent from the list and resolve to 404 on direct access, so
    they never appear — as failing, partial, or otherwise — anywhere a viewer
    (incl. the /compliance page and TV/NOC screen) looks.
    """

    permission_classes = [HasCapability("framework:view")]
    lookup_field = "key"

    def _get_in_scope(self, key):
        """Fetch a framework by key only if it is in scope; else ``None`` (404)."""
        if not is_framework_applicable(key):
            return None
        return RegulatoryFramework.objects.filter(key=key).prefetch_related("controls").first()

    def list(self, request):
        frameworks = applicable_frameworks(
            RegulatoryFramework.objects.filter(enabled=True)).prefetch_related("controls")
        try:
            return Response([framework_summary(fw) for fw in frameworks])
        except Exception as exc:  # noqa: BLE001 — evaluators touch live data; never leak detail
            return internal_error_response(exc, logger, "framework list")

    def retrieve(self, request, key=None):
        fw = self._get_in_scope(key)
        if fw is None:
            return Response({"error": "Framework not found."}, status=status.HTTP_404_NOT_FOUND)
        try:
            return Response(evaluate_framework(fw))
        except Exception as exc:  # noqa: BLE001
            return internal_error_response(exc, logger, f"framework assessment {key}")

    @action(detail=True, methods=["get"], url_path="report")
    def report(self, request, key=None):
        """Generate a PDF evidence package for the framework."""
        fw = self._get_in_scope(key)
        if fw is None:
            return Response({"error": "Framework not found."}, status=status.HTTP_404_NOT_FOUND)
        try:
            from .pdf import build_evidence_pdf
        except ImportError:
            return Response(
                {"error": "PDF generation unavailable (reportlab not installed)."},
                status=status.HTTP_501_NOT_IMPLEMENTED)
        try:
            report_data = evaluate_framework(fw)
            generated_at = timezone.now().strftime("%Y-%m-%d %H:%M UTC")
            pdf_bytes = build_evidence_pdf(report_data, generated_at=generated_at)
        except Exception as exc:  # noqa: BLE001 — scrub PDF/eval errors
            return internal_error_response(exc, logger, f"framework PDF {key}")
        resp = HttpResponse(pdf_bytes, content_type="application/pdf")
        fname = f"spane-{fw.key}-evidence-{timezone.now():%Y%m%d}.pdf"
        resp["Content-Disposition"] = f'attachment; filename="{fname}"'
        return resp
