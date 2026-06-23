import logging

from drf_spectacular.utils import extend_schema
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.mixins import ListModelMixin, RetrieveModelMixin
from rest_framework.permissions import SAFE_METHODS
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.viewsets import GenericViewSet

from apps.core.errors import safe_detail
from apps.core.permissions import CapabilityViewSetMixin, HasCapability
from .models import (
    ApprovedOSVersion,
    CompliancePolicy,
    CompliancePolicyRule,
    ComplianceResult,
    ComplianceTemplate,
    ComplianceTemplateResult,
    DiscoveredPlatformModel,
    InterfaceComplianceResult,
    InterfaceComplianceRule,
    RoleConsistencyRule,
)
from .serializers import (
    ApprovedOSVersionSerializer,
    CompliancePolicyRuleSerializer,
    CompliancePolicySerializer,
    ComplianceResultSerializer,
    ComplianceTemplateResultSerializer,
    ComplianceTemplateSerializer,
    DiscoveredPlatformModelSerializer,
    InterfaceComplianceResultSerializer,
    InterfaceComplianceRuleSerializer,
    RoleConsistencyRuleSerializer,
)

logger = logging.getLogger(__name__)


def _refresh_stored_scores(run_result: dict) -> None:
    """Re-persist the weighted DeviceComplianceScore for every device a rule run
    touched, so the device list reflects the updated interface/role result.

    Best-effort: a shared role_cache evaluates each role rule once across the
    set; any per-device failure is logged, never raised.
    """
    ids = {r.get("device_id") for r in (run_result.get("results") or []) if r.get("device_id")}
    if not ids:
        return
    from apps.devices.models import Device

    from .device_score import run_and_store_compliance
    role_cache: dict = {}
    for device in Device.objects.filter(id__in=ids):
        try:
            run_and_store_compliance(device, role_cache=role_cache)
        except Exception as exc:  # noqa: BLE001 — scoring must not break the run
            logger.warning("score refresh failed for %s: %s", device.hostname, exc)


class CompliancePolicyViewSet(CapabilityViewSetMixin, viewsets.ModelViewSet):
    view_capability = "compliance:view"
    write_capability = "compliance:edit"
    queryset = CompliancePolicy.objects.prefetch_related("rules").all()
    serializer_class = CompliancePolicySerializer
    filterset_fields = ["is_active"]
    search_fields = ["name"]


class CompliancePolicyRuleViewSet(CapabilityViewSetMixin, viewsets.ModelViewSet):
    view_capability = "compliance:view"
    write_capability = "compliance:edit"
    queryset = CompliancePolicyRule.objects.select_related("policy").all()
    serializer_class = CompliancePolicyRuleSerializer
    filterset_fields = ["policy", "check_type", "is_active"]


class ComplianceResultViewSet(CapabilityViewSetMixin, ListModelMixin, RetrieveModelMixin, GenericViewSet):
    view_capability = "compliance:view"
    queryset = ComplianceResult.objects.select_related("device", "policy", "rule").all()
    serializer_class = ComplianceResultSerializer
    filterset_fields = ["device", "policy", "outcome"]
    ordering_fields = ["created_at"]


# ── Template-based compliance ───────────────────────────────────────────────────

class ComplianceTemplateViewSet(CapabilityViewSetMixin, viewsets.ModelViewSet):
    """
    Manage compliance templates — Jinja2 templates of expected config, scoped by
    role / platform / site. The `preview/` action renders a template for a device.
    """

    view_capability = "compliance:view"
    write_capability = "compliance:edit"
    queryset = ComplianceTemplate.objects.select_related("role", "site").all()
    serializer_class = ComplianceTemplateSerializer
    filterset_fields = ["platform", "role", "site", "enabled"]
    search_fields = ["name", "description"]
    ordering_fields = ["name", "created_at"]
    ordering = ["name"]

    # Authoring a template = writing a server-side-rendered Jinja2 string (an SSTI
    # surface even behind the sandbox), so creating/editing/deleting templates is
    # admin-only. Reading (list/retrieve) and rendering (preview) stay open to the
    # operational roles that need to view templates.
    _ADMIN_ACTIONS = frozenset({"create", "update", "partial_update", "destroy"})

    def get_permissions(self):
        if self.action in self._ADMIN_ACTIONS:
            return [HasCapability("compliance:template:edit")()]
        return super().get_permissions()

    def perform_create(self, serializer):
        user = self.request.user if self.request.user.is_authenticated else None
        serializer.save(created_by=user)

    @extend_schema(request=None, responses=None,
                   summary="Render this template for a device (no save)")
    @action(detail=True, methods=["post"])
    def preview(self, request, pk=None):
        """Render the template for {device_id} using its overrides + context."""
        from apps.devices.models import Device

        from .engine import ComplianceEngine
        from .models import DeviceComplianceOverride

        template = self.get_object()
        device_id = request.data.get("device_id")
        device = Device.objects.filter(pk=device_id).first()
        if not device:
            return Response({"error": "device_id not found"}, status=status.HTTP_400_BAD_REQUEST)
        overrides = (
            DeviceComplianceOverride.objects
            .filter(device=device, template=template)
            .values_list("variables", flat=True)
            .first()
        ) or {}
        try:
            rendered = ComplianceEngine().render_template(template, device, overrides)
        except Exception as exc:  # noqa: BLE001 — template errors become a 400 for the UI
            return Response({"error": safe_detail(exc, logger, "render compliance template",
                            public="Template render error (check the template syntax and variables).")},
                            status=status.HTTP_400_BAD_REQUEST)
        return Response({"device_id": device.id, "hostname": device.hostname, "rendered": rendered})


class ComplianceTemplateResultViewSet(CapabilityViewSetMixin, ListModelMixin, RetrieveModelMixin, GenericViewSet):
    """Read template-compliance results. Filter by device, template, status."""

    view_capability = "compliance:view"
    queryset = ComplianceTemplateResult.objects.select_related("device", "template").all()
    serializer_class = ComplianceTemplateResultSerializer
    filterset_fields = ["device", "template", "status"]
    ordering_fields = ["checked_at", "score"]
    ordering = ["-checked_at"]


class ComplianceCheckView(APIView):
    """
    Run template compliance checks and save results.

    POST body:
      {"device_id": N}    → all applicable templates for one device
      {"template_id": N}  → one template across all active devices
      {}                  → every template against every active device

    Returns {"checked", "compliant", "non_compliant", "error"}.
    """

    permission_classes = [HasCapability("compliance:run")]

    @extend_schema(request=None, responses=None, summary="Run compliance checks")
    def post(self, request):
        from apps.devices.models import Device

        from .engine import ComplianceEngine, get_templates_for_device

        device_id = request.data.get("device_id")
        template_id = request.data.get("template_id")
        engine = ComplianceEngine()

        results: list[ComplianceTemplateResult] = []

        if device_id:
            device = Device.objects.filter(pk=device_id).first()
            if not device:
                return Response({"error": "device_id not found"}, status=status.HTTP_400_BAD_REQUEST)
            templates = get_templates_for_device(device)
            if template_id:
                templates = [t for t in templates if t.id == int(template_id)]
            results = [self._run(engine, device, t) for t in templates]
        else:
            devices = Device.objects.filter(status=Device.Status.ACTIVE)
            if template_id:
                template = ComplianceTemplate.objects.filter(pk=template_id, enabled=True).first()
                if not template:
                    return Response({"error": "template_id not found or disabled"},
                                    status=status.HTTP_400_BAD_REQUEST)
                for device in devices:
                    if template in get_templates_for_device(device):
                        results.append(self._run(engine, device, template))
            else:
                for device in devices:
                    for template in get_templates_for_device(device):
                        results.append(self._run(engine, device, template))

        compliant = sum(1 for r in results if r.status == ComplianceTemplateResult.Status.COMPLIANT)
        non_compliant = sum(1 for r in results if r.status == ComplianceTemplateResult.Status.NON_COMPLIANT)
        errored = sum(1 for r in results if r.status == ComplianceTemplateResult.Status.ERROR)
        return Response({
            "checked": len(results),
            "compliant": compliant,
            "non_compliant": non_compliant,
            "error": errored,
        })

    @staticmethod
    def _run(engine, device, template) -> ComplianceTemplateResult:
        try:
            result = engine.check_device(device, template)
        except Exception as exc:  # noqa: BLE001
            # Log server-side; never persist/return exception text in the finding
            # (CodeQL py/stack-trace-exposure — the result is exposed via the API).
            logger.warning("compliance check failed for %s / %s: %s",
                           device.hostname, template.name, exc)
            result = engine._error_result(
                device, template, "Compliance check error (details in server logs).")
        result.save()
        return result


class ApprovedOSVersionViewSet(CapabilityViewSetMixin, viewsets.ModelViewSet):
    """CRUD over OS-version policy entries. Recomputes cached fleet statuses on
    any change so the inventory page reflects the new policy immediately."""

    view_capability = "compliance:view"
    write_capability = "compliance:edit"
    queryset = ApprovedOSVersion.objects.all()
    serializer_class = ApprovedOSVersionSerializer
    filterset_fields = ["platform", "status", "is_regex"]
    ordering_fields = ["platform", "version_pattern", "status", "created_at"]

    def _recompute(self):
        from .os_policy import recompute_statuses
        try:
            recompute_statuses()
        except Exception as exc:  # noqa: BLE001 — never fail the write on a refresh hiccup
            logger.warning("OS policy: recompute_statuses failed: %s", exc)

    def perform_create(self, serializer):
        serializer.save()
        self._recompute()

    def perform_update(self, serializer):
        serializer.save()
        self._recompute()

    def perform_destroy(self, instance):
        instance.delete()
        self._recompute()

    @action(detail=False, methods=["post"], url_path="sync-from-inventory")
    def sync_from_inventory(self, request):
        """Auto-create placeholder policies for every OS version in inventory."""
        from .os_policy import seed_os_versions_from_inventory
        result = seed_os_versions_from_inventory()
        self._recompute()
        result["message"] = (
            f"Discovered {result['created']} new OS version(s) from "
            f"{result['devices']} device(s) in inventory"
        )
        return Response(result)


class DiscoveredPlatformModelViewSet(CapabilityViewSetMixin, ListModelMixin, RetrieveModelMixin, GenericViewSet):
    """Read the fleet platform/model/version inventory. `refresh` rebuilds it
    from current devices; the detail `devices` action lists matching devices."""

    view_capability = "compliance:view"
    write_capability = "compliance:edit"
    queryset = DiscoveredPlatformModel.objects.all()
    serializer_class = DiscoveredPlatformModelSerializer
    filterset_fields = ["platform", "os_status"]
    ordering_fields = ["platform", "model", "os_version", "device_count"]

    @action(detail=False, methods=["post"], url_path="refresh")
    def refresh(self, request):
        from .os_policy import refresh_discovered_platforms
        count = refresh_discovered_platforms()
        return Response({"combos": count})

    @action(detail=True, methods=["get"], url_path="devices")
    def devices(self, request, pk=None):
        """Devices running this exact platform/model/version combo."""
        from apps.devices.models import Device
        from apps.devices.serializers import DeviceListSerializer

        combo = self.get_object()
        devices = Device.objects.filter(
            platform=combo.platform, model=combo.model or "",
            os_version=combo.os_version or "",
        ).select_related("site", "role")
        return Response(DeviceListSerializer(devices, many=True).data)


class OSComplianceSummaryView(APIView):
    """Fleet-wide OS-version compliance tallies for the dashboard donut."""

    permission_classes = [HasCapability("compliance:view")]

    @extend_schema(request=None, responses=None, summary="OS compliance summary")
    def get(self, request):
        from .os_policy import os_summary
        return Response(os_summary())


class InterfaceComplianceRuleViewSet(CapabilityViewSetMixin, viewsets.ModelViewSet):
    """
    LLDP-aware interface compliance rules. `run/` evaluates the rule against
    every matching switch interface and returns + persists the results.
    """

    view_capability = "compliance:view"
    write_capability = "compliance:edit"
    queryset = InterfaceComplianceRule.objects.prefetch_related("results").all()
    serializer_class = InterfaceComplianceRuleSerializer
    filterset_fields = ["trigger", "platform", "enabled"]
    search_fields = ["name", "description"]
    ordering = ["name"]

    def get_permissions(self):
        if self.action == "run":
            return [HasCapability("compliance:run")()]
        return super().get_permissions()

    @extend_schema(request=None, responses=None)
    @action(detail=True, methods=["post"])
    def run(self, request, pk=None):
        from .interface_compliance import run_interface_compliance
        result = run_interface_compliance(self.get_object())
        _refresh_stored_scores(result)
        return Response(result)


class InterfaceComplianceResultViewSet(CapabilityViewSetMixin, ListModelMixin, RetrieveModelMixin, GenericViewSet):
    """Read-only interface-compliance results; filter by device_id / rule_id."""

    view_capability = "compliance:view"
    serializer_class = InterfaceComplianceResultSerializer
    queryset = InterfaceComplianceResult.objects.select_related("device", "rule").all()

    def get_queryset(self):
        qs = super().get_queryset()
        did = self.request.query_params.get("device_id")
        rid = self.request.query_params.get("rule_id")
        if did:
            qs = qs.filter(device_id=did)
        if rid:
            # Explicit rule lookup (rule-management view): show that rule's own
            # results, enabled or not.
            qs = qs.filter(rule_id=rid)
        else:
            # Device-scoped / unfiltered: hide results from disabled rules so
            # they don't surface on the device compliance view.
            qs = qs.filter(rule__enabled=True)
        return qs


class RoleConsistencyRuleViewSet(CapabilityViewSetMixin, viewsets.ModelViewSet):
    """
    Cross-device consistency rules (VLAN/NTP/DNS/SNMP/AAA/banner). `run/` compares
    the configured item across the scoped group and returns drift per device.
    """

    view_capability = "compliance:view"
    write_capability = "compliance:edit"
    queryset = RoleConsistencyRule.objects.select_related("role", "site").all()
    serializer_class = RoleConsistencyRuleSerializer
    filterset_fields = ["check_type", "platform", "role", "site", "enabled"]
    search_fields = ["name", "description"]
    ordering = ["name"]

    def get_permissions(self):
        if self.action == "run":
            return [HasCapability("compliance:run")()]
        return super().get_permissions()

    @extend_schema(request=None, responses=None)
    @action(detail=True, methods=["post"])
    def run(self, request, pk=None):
        from .role_consistency import run_role_consistency
        result = run_role_consistency(self.get_object())
        _refresh_stored_scores(result)
        return Response(result)


class ComplianceRunAllView(APIView):
    """Start a fleet-wide compliance run (background) or report its progress.

    POST {device_ids?: [...]} → start a run over all active devices (or just the
    given subset); returns the initial status. 409 if a run is already going.
    GET → current/last run status for polling.
    """

    def get_permissions(self):
        if self.request.method in SAFE_METHODS:
            return [HasCapability("compliance:view")()]
        return [HasCapability("compliance:run")()]

    @extend_schema(request=None, responses=None, summary="Run compliance for all (or selected) devices")
    def post(self, request):
        from .runner import start_run_all
        ids = request.data.get("device_ids") or None
        if ids is not None:
            if not isinstance(ids, list):
                return Response({"error": "device_ids must be a list"}, status=status.HTTP_400_BAD_REQUEST)
            ids = [int(i) for i in ids]
        started, run_status = start_run_all(ids)
        if not started:
            return Response({"error": "A compliance run is already in progress.", **run_status},
                            status=status.HTTP_409_CONFLICT)
        return Response(run_status, status=status.HTTP_202_ACCEPTED)

    def get(self, request):
        from .runner import get_status
        return Response(get_status())


class ComplianceRunAllStatusView(APIView):
    """GET → current/last fleet compliance run status (polled by the UI)."""

    permission_classes = [HasCapability("compliance:view")]

    @extend_schema(responses=None, summary="Fleet compliance run status")
    def get(self, request):
        from .runner import get_status
        return Response(get_status())


class ComplianceRunDeviceView(APIView):
    """POST → re-run compliance for one device and return its weighted score."""

    permission_classes = [HasCapability("compliance:run")]

    @extend_schema(request=None, responses=None, summary="Run compliance for one device")
    def post(self, request, device_id=None):
        from apps.devices.models import Device

        from .runner import run_one
        device = Device.objects.filter(pk=device_id).first()
        if device is None:
            return Response({"error": "device not found"}, status=status.HTTP_404_NOT_FOUND)
        try:
            data = run_one(device)
        except Exception as exc:  # noqa: BLE001 — scrub; scoring can reach live devices
            return Response({"error": safe_detail(exc, logger, f"compliance run {device.pk}")},
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        return Response({
            "device_id": device.pk,
            "hostname": device.hostname,
            "score": data["score"],
            "grade": data["grade"],
            "breakdown": data["breakdown"],
        })
