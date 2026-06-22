from __future__ import annotations

import logging

from django.shortcuts import get_object_or_404
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.core.errors import internal_error_response
from apps.core.permissions import AdminOnly
from apps.devices.models import Device

from .models import ConfigPushTemplate
from .push import audit_push, push_template_to_device
from .render import mask_sensitive_output, render_template
from .serializers import ConfigPushTemplateSerializer

logger = logging.getLogger(__name__)


class ConfigPushTemplateViewSet(viewsets.ModelViewSet):
    """CRUD + preview/push for editable config-push templates.

    Filter by ``?category`` / ``?platform`` / ``?enabled`` / ``?builtin``.
    Admin-only: rendering and pushing config to devices is a privileged action.
    """

    queryset = ConfigPushTemplate.objects.select_related("created_by").all()
    serializer_class = ConfigPushTemplateSerializer
    permission_classes = [AdminOnly]
    filterset_fields = ["category", "platform", "enabled", "builtin"]
    search_fields = ["name", "description"]
    ordering_fields = ["name", "category", "updated_at"]

    def perform_create(self, serializer):
        user = self.request.user if self.request.user.is_authenticated else None
        serializer.save(created_by=user)

    def destroy(self, request, *args, **kwargs):
        template = self.get_object()
        if template.builtin:
            return Response(
                {"detail": "Built-in templates cannot be deleted. Disable it instead."},
                status=status.HTTP_403_FORBIDDEN,
            )
        return super().destroy(request, *args, **kwargs)

    def _merged_variables(self, template, request) -> dict:
        """Template defaults (incl. OpenBao secrets) overlaid with request values."""
        overrides = request.data.get("variables") or {}
        return {**template.default_variables(include_secrets=True), **overrides}

    @action(detail=True, methods=["post"], url_path="preview")
    def preview(self, request, pk=None):
        """Render this template for a device; sensitive values are masked out.

        Accepts an optional ``template_content`` override so the editor can
        preview unsaved edits without persisting them first.
        """
        template = self.get_object()
        device = get_object_or_404(Device, pk=request.data.get("device_id"))
        variables = self._merged_variables(template, request)
        content = request.data.get("template_content") or template.template_content
        try:
            rendered = render_template(content, device, variables)
        except Exception as exc:
            return internal_error_response(
                exc, logger, f"preview template {template.pk}",
                status_code=status.HTTP_400_BAD_REQUEST,
                public_message="Template render failed — check the Jinja2 syntax and variables.",
            )
        return Response({
            "device": device.hostname,
            "rendered": mask_sensitive_output(rendered, variables),
        })

    @action(detail=True, methods=["post"], url_path="push")
    def push(self, request, pk=None):
        """Render + push this template to one or more devices (audited per device)."""
        from django.conf import settings as dj_settings

        template = self.get_object()
        device_ids = request.data.get("device_ids") or []
        if not isinstance(device_ids, list) or not device_ids:
            return Response({"detail": "device_ids (non-empty list) is required."},
                            status=status.HTTP_400_BAD_REQUEST)

        devices = list(
            Device.objects.filter(pk__in=device_ids)
            .select_related("site", "role", "credential_profile")
        )

        # Master safety switch: block the push but still audit every attempt.
        if not getattr(dj_settings, "ALLOW_CONFIG_PUSH", False):
            for device in devices:
                audit_push(template, device, request, False,
                           "config push is disabled (ALLOW_CONFIG_PUSH=false)")
            return Response(
                {"success": False, "succeeded": 0, "total": len(devices), "results": [],
                 "error": "Config push is disabled. Set ALLOW_CONFIG_PUSH=true to enable."},
                status=status.HTTP_403_FORBIDDEN,
            )

        variables = self._merged_variables(template, request)
        results = [push_template_to_device(template, device, variables, request)
                   for device in devices]
        succeeded = sum(1 for r in results if r["success"])
        return Response({
            "success": bool(results) and succeeded == len(results),
            "succeeded": succeeded,
            "total": len(results),
            "results": results,
        })
