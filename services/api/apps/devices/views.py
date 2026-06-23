import logging

from django.db.models import Count, Q
from drf_spectacular.utils import OpenApiParameter, extend_schema, inline_serializer
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

logger = logging.getLogger(__name__)

from apps.core.errors import internal_error_response, safe_detail
from apps.core.permissions import CapabilityViewSetMixin, HasCapability
from apps.credentials import vault
from apps.credentials.models import CredentialProfile

from . import detect, fingerprint
from .models import (
    Device, DeviceGroup, DeviceRole, DiscoveredDevice, DiscoveryJob,
    HostnameRule, LLDPNeighbor, ManualTopologyLink, Site,
)
from .serializers import (
    DetectPlatformRequestSerializer,
    DetectPlatformResponseSerializer,
    DeviceGroupSerializer,
    DeviceListSerializer,
    DeviceRoleSerializer,
    DeviceSerializer,
    DiscoveredDeviceSerializer,
    DiscoveryJobSerializer,
    HostnameRuleSerializer,
    HostnameRuleTestSerializer,
    LLDPNeighborSerializer,
    ManualTopologyLinkSerializer,
    SiteSerializer,
    TestConnectionRequestSerializer,
    TestConnectionResponseSerializer,
)


def _truthy(value) -> bool:
    """Loose truthiness for query-string flags (?show_all=true|1|yes)."""
    return str(value).lower() in ("1", "true", "yes", "on")


def _ssh_creds(profile_id):
    """Return (profile, ssh_password) for a credential profile id, or (None, None)."""
    profile = CredentialProfile.objects.filter(pk=profile_id).first()
    if not profile:
        return None, None
    secrets = vault.read_secret(profile.vault_path) if profile.vault_path else {}
    return profile, secrets.get("ssh_password", "")


class SiteViewSet(CapabilityViewSetMixin, viewsets.ModelViewSet):
    """
    Manage sites/locations — a hierarchy of datacenters, campuses and branches.

    Sites carry address/geo, contact details and an optional parent for
    hierarchy. Filter by `site_type` or `parent_site`; search by name/city. The
    `devices/` action lists the devices located at a site.
    """

    view_capability = "device:view"
    write_capability = "device:edit"

    queryset = Site.objects.select_related("parent_site").annotate(
        device_count=Count("devices", distinct=True),
        devices_up=Count(
            "devices",
            filter=Q(devices__is_reachable=True, devices__status=Device.Status.ACTIVE),
            distinct=True,
        ),
        devices_down=Count(
            "devices",
            filter=Q(devices__is_reachable=False)
            | Q(devices__status__in=[Device.Status.INACTIVE, Device.Status.UNREACHABLE]),
            distinct=True,
        ),
        devices_unknown=Count(
            "devices", filter=Q(devices__is_reachable__isnull=True), distinct=True
        ),
    ).order_by("name")
    serializer_class = SiteSerializer
    filterset_fields = ["site_type", "parent_site"]
    search_fields = ["name", "city", "address"]
    ordering_fields = ["name", "site_type", "created_at"]

    _AUDIT_LABELS = {
        "name": "Name", "site_type": "Type", "description": "Description",
        "location": "Location", "address": "Address", "city": "City",
        "state": "State", "country": "Country", "contact_name": "Contact Name",
        "contact_email": "Contact Email", "contact_phone": "Contact Phone",
        "notes": "Notes", "parent_site": "Parent Site",
        "default_collector": "Default Collector",
    }

    @staticmethod
    def _snapshot(site):
        return {f: getattr(site, f) or "" for f in (
            "name", "site_type", "description", "location", "address", "city",
            "state", "country", "contact_name", "contact_email", "contact_phone",
            "notes",
        )} | {
            "parent_site": str(site.parent_site) if site.parent_site_id else None,
            "default_collector": str(site.default_collector) if site.default_collector_id else None,
        }

    def perform_create(self, serializer):
        site = serializer.save()
        from apps.core.audit import log_event
        from apps.core.models import AuditLog
        log_event(AuditLog.EventType.SITE_CREATED, request=self.request, target=site,
                  description=f'Site "{site.name}" created')

    def update(self, request, *args, **kwargs):
        from apps.core.audit import describe_changes, diff_model_changes, log_event
        from apps.core.models import AuditLog
        site = self.get_object()
        before = self._snapshot(site)
        response = super().update(request, *args, **kwargs)
        site.refresh_from_db()
        changes = diff_model_changes(before, self._snapshot(site), self._AUDIT_LABELS)
        if changes:
            log_event(AuditLog.EventType.SITE_UPDATED, request=request, target=site,
                      description=describe_changes(f'Site "{site.name}"', changes),
                      metadata={"changes": changes})
        return response

    def perform_destroy(self, instance):
        from apps.core.audit import log_event
        from apps.core.models import AuditLog
        name = instance.name
        log_event(AuditLog.EventType.SITE_DELETED, request=self.request, target=instance,
                  description=f'Site "{name}" deleted')
        instance.delete()

    @action(detail=True, methods=["get"], url_path="devices")
    def devices(self, request, pk=None):
        """List devices located at this site."""
        site = self.get_object()
        devices = site.devices.all()
        return Response(DeviceListSerializer(devices, many=True).data)

    @extend_schema(request=None, responses=None)
    @action(detail=True, methods=["get", "post"], url_path="credentials")
    def credentials(self, request, pk=None):
        """List (GET) or add (POST) credential-profile assignments for this site."""
        from apps.credentials.models import SiteCredential
        from apps.credentials.serializers import SiteCredentialSerializer
        site = self.get_object()
        if request.method == "GET":
            qs = SiteCredential.objects.filter(site=site).select_related(
                "credential_profile", "role").order_by("priority")
            return Response(SiteCredentialSerializer(qs, many=True).data)
        ser = SiteCredentialSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        ser.save(site=site)
        return Response(ser.data, status=status.HTTP_201_CREATED)

    @extend_schema(request=None, responses=None)
    @action(detail=True, methods=["delete"], url_path=r"credentials/(?P<cred_id>[^/.]+)")
    def delete_credential(self, request, pk=None, cred_id=None):
        """Remove a credential assignment from this site."""
        from apps.credentials.models import SiteCredential
        site = self.get_object()
        deleted, _ = SiteCredential.objects.filter(site=site, pk=cred_id).delete()
        if not deleted:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @extend_schema(request=None, responses=None)
    @action(detail=True, methods=["get"], url_path="suggest-credential")
    def suggest_credential(self, request, pk=None):
        """Suggest the credential a new device at this site (+ optional role) would
        inherit. ``?role=<id>``. Returns {credential_profile, name, scope} or nulls."""
        from apps.credentials.site_resolve import resolve_credential
        site = self.get_object()
        role_id = request.query_params.get("role") or None
        profile = resolve_credential(site.id, role_id)
        if not profile:
            return Response({"credential_profile": None, "name": None, "scope": None})
        # Describe the match for the UI hint.
        from apps.credentials.models import SiteCredential
        role_match = role_id and SiteCredential.objects.filter(
            site=site, role_id=role_id, credential_profile=profile).exists()
        return Response({
            "credential_profile": profile.id, "name": profile.name,
            "scope": "role" if role_match else "all roles",
        })


class DeviceGroupViewSet(CapabilityViewSetMixin, viewsets.ModelViewSet):
    view_capability = "device:view"
    write_capability = "device:edit"

    queryset = DeviceGroup.objects.all()
    serializer_class = DeviceGroupSerializer


class DeviceRoleViewSet(CapabilityViewSetMixin, viewsets.ModelViewSet):
    """
    Manage device roles — labelled, colour-coded classifications (Core Switch,
    Firewall, Router, …) shown as bubbles in the device list and detail pages.

    A role assigned to one or more devices cannot be deleted; reassign those
    devices first.
    """

    view_capability = "device:view"
    write_capability = "device:edit"

    queryset = DeviceRole.objects.all()
    serializer_class = DeviceRoleSerializer
    search_fields = ["name", "description"]
    ordering_fields = ["name", "created_at"]
    ordering = ["name"]

    def destroy(self, request, *args, **kwargs):
        role = self.get_object()
        in_use = role.devices.count()
        if in_use:
            return Response(
                {"error": f"Role is assigned to {in_use} device(s). Reassign them before deleting."},
                status=status.HTTP_409_CONFLICT,
            )
        return super().destroy(request, *args, **kwargs)


class DeviceViewSet(CapabilityViewSetMixin, viewsets.ModelViewSet):
    """
    Manage network devices — the core inventory of spane.

    Full CRUD over devices (routers, switches, firewalls, etc.). List responses
    use a lightweight serializer; retrieve returns the full record including site,
    groups and associated credential profiles. Filter by `status`, `platform`,
    `vendor` or `site`; search across hostname, IP and serial number. The
    `topology/` action returns nodes + edges for the network map.
    """

    view_capability = "device:view"
    write_capability = "device:edit"

    queryset = Device.objects.select_related("site", "role").prefetch_related("groups").all()
    filterset_fields = ["status", "platform", "vendor", "site", "role"]
    search_fields = ["hostname", "ip_address", "serial_number"]
    ordering_fields = [
        "hostname", "status", "ip_address", "vendor", "platform", "model",
        "os_version", "serial_number", "last_seen", "created_at", "site__name",
        "compliance_score",
    ]
    ordering = ["hostname"]

    # Letter grade → [low, high) score band for the ?compliance_grade= filter.
    _GRADE_BANDS = {"A": (90, None), "B": (80, 90), "C": (70, 80), "D": (60, 70), "F": (0, 60)}

    def get_queryset(self):
        from django.db.models import FloatField, OuterRef, Subquery

        from apps.compliance.models import DeviceComplianceScore

        # Annotate the stored WEIGHTED compliance score per device (template +
        # interface + role + startup, renormalised) in a single subquery — the
        # same number the Compliance tab shows, not the template-only score.
        # Avoids an N+1 over the device list (no live scoring).
        latest_score = (DeviceComplianceScore.objects
                        .filter(device=OuterRef("pk"))
                        .values("score")[:1])
        qs = super().get_queryset().annotate(
            compliance_score=Subquery(latest_score, output_field=FloatField()))

        p = self.request.query_params
        checked = p.get("compliance_checked")
        if checked == "false":
            qs = qs.filter(compliance_score__isnull=True)
        elif checked == "true":
            qs = qs.filter(compliance_score__isnull=False)

        grade = p.get("compliance_grade")
        if grade == "none":
            qs = qs.filter(compliance_score__isnull=True)
        elif grade in self._GRADE_BANDS:
            lo, hi = self._GRADE_BANDS[grade]
            qs = qs.filter(compliance_score__gte=lo)
            if hi is not None:
                qs = qs.filter(compliance_score__lt=hi)

        lt = p.get("compliance_score__lt")
        if lt:
            try:
                qs = qs.filter(compliance_score__lt=float(lt))
            except (TypeError, ValueError):
                pass
        return qs

    def get_serializer_class(self):
        if self.action == "list":
            return DeviceListSerializer
        return DeviceSerializer

    def create(self, request, *args, **kwargs):
        """
        Upsert by hostname so device identity is stable: re-adding a device with
        an existing hostname updates that row (reusing its PK and all references)
        instead of erroring on the unique constraint or creating a duplicate.

        Returns 200 when an existing device was updated, 201 when created.
        (Hostname is globally unique today; once the tenant field lands this
        should key on hostname+tenant — see CLAUDE.md RBAC & Multi-Tenancy.)
        """
        hostname = request.data.get("hostname")
        existing = Device.objects.filter(hostname=hostname).first() if hostname else None
        # Passing instance=existing makes this a full update: the unique
        # validators exclude the instance and the PK is preserved.
        serializer = self.get_serializer(instance=existing, data=request.data)
        serializer.is_valid(raise_exception=True)
        device = serializer.save()
        from apps.core.audit import log_event
        from apps.core.models import AuditLog
        log_event(
            AuditLog.EventType.DEVICE_UPDATED if existing else AuditLog.EventType.DEVICE_CREATED,
            request=request, target=device,
            description=f"Device {device.hostname} {'updated' if existing else 'created'}",
            metadata={"ip": str(device.management_ip or device.ip_address), "platform": device.platform},
        )
        return Response(
            serializer.data,
            status=status.HTTP_200_OK if existing else status.HTTP_201_CREATED,
        )

    def update(self, request, *args, **kwargs):
        """PUT/PATCH a device, recording a field-level before/after diff in the
        audit log (``partial_update`` routes through here too). Only emits an
        audit event when an audited field actually changed."""
        from apps.core.audit import (
            DEVICE_FIELD_LABELS, describe_changes, diff_model_changes,
            log_event, snapshot_device,
        )
        from apps.core.models import AuditLog
        instance = self.get_object()
        before = snapshot_device(instance)
        response = super().update(request, *args, **kwargs)
        instance.refresh_from_db()
        after = snapshot_device(instance)
        changes = diff_model_changes(before, after, DEVICE_FIELD_LABELS)
        if changes:
            log_event(
                AuditLog.EventType.DEVICE_UPDATED, request=request, target=instance,
                description=describe_changes(instance.hostname, changes),
                metadata={"changes": changes},
            )
        return response

    def perform_destroy(self, instance):
        from apps.core.audit import log_event
        from apps.core.models import AuditLog
        name = instance.hostname
        log_event(AuditLog.EventType.DEVICE_DELETED, request=self.request, target=instance,
                  description=f"Device {name} deleted")
        instance.delete()

    @extend_schema(
        request=TestConnectionRequestSerializer,
        responses=TestConnectionResponseSerializer,
        summary="Probe an IP and best-effort fingerprint a device",
    )
    @action(detail=False, methods=["post"], url_path="test-connection")
    def test_connection(self, request):
        """
        Probe management ports on an IP and infer the vendor from the SSH banner.
        Used by the Add-Device wizard's auto-detect step. Full platform/OS/model
        detection happens later in the poller (needs SNMP/credentials).
        """
        req = TestConnectionRequestSerializer(data=request.data)
        req.is_valid(raise_exception=True)
        ip = req.validated_data["ip"]
        result = fingerprint.fingerprint(ip)
        # If a credential profile is supplied, also run SSHDetect to fill in
        # vendor/platform/os_version (otherwise they stay null from the probe).
        profile_id = req.validated_data.get("credential_profile_id")
        if profile_id:
            profile, password = _ssh_creds(profile_id)
            if profile and profile.ssh_enabled:
                det = detect.detect_platform(ip, profile.ssh_username, password, profile.ssh_port)
                if det.get("detected"):
                    result.update({
                        "vendor": det.get("vendor") or result["vendor"],
                        "platform": det.get("platform"),
                        "os_version": det.get("os_version"),
                        "model": det.get("model"),
                    })
        return Response(TestConnectionResponseSerializer(result).data)

    @extend_schema(
        request=DetectPlatformRequestSerializer,
        responses=DetectPlatformResponseSerializer,
        summary="Auto-detect a device's platform via Netmiko SSHDetect",
    )
    @action(detail=False, methods=["post"], url_path="detect-platform")
    def detect_platform(self, request):
        """
        SSH to the device with the given credential profile, run Netmiko
        SSHDetect to identify the platform, then read show-version for OS/model/
        serial. Returns {detected, device_type, vendor, platform, …} or
        {detected: false, error}.
        """
        req = DetectPlatformRequestSerializer(data=request.data)
        req.is_valid(raise_exception=True)
        profile, password = _ssh_creds(req.validated_data["credential_profile_id"])
        if not profile:
            return Response({"detected": False, "error": "credential profile not found"},
                            status=status.HTTP_400_BAD_REQUEST)
        if not profile.ssh_enabled:
            return Response({"detected": False, "error": "ssh_not_enabled"},
                            status=status.HTTP_400_BAD_REQUEST)
        result = detect.detect_platform(
            req.validated_data["ip"], profile.ssh_username, password, profile.ssh_port
        )
        return Response(DetectPlatformResponseSerializer(result).data)

    @extend_schema(
        summary="Time-series telemetry metrics for a device (from InfluxDB)",
        parameters=[
            OpenApiParameter("metric", str, description="cpu|memory|uptime|interfaces|all"),
            OpenApiParameter("period", str, description="1h|6h|24h|7d (default 1h)"),
        ],
        responses=None,
    )
    @action(detail=True, methods=["get"], url_path="metrics")
    def metrics(self, request, pk=None):
        """Latest snapshot + windowed time-series for the device's SNMP metrics."""
        from . import metrics_influx
        device = self.get_object()
        data = metrics_influx.query_device_metrics(
            str(device.id),
            request.query_params.get("metric", "all"),
            request.query_params.get("period", "1h"),
        )
        # LLDP neighbours from discovered TopologyLink rows (either direction),
        # independent of whether interface discovery populated the per-interface
        # lldp_* fields — so a device with topology links but no per-interface
        # LLDP metadata still shows its neighbours.
        data["lldp_neighbors"] = self._lldp_neighbors(device)
        return Response(data)

    @extend_schema(
        summary="UniFi AP telemetry: latest radio/health snapshot + time-series",
        parameters=[OpenApiParameter("period", str, description="1h|6h|24h|7d (default 1h)")],
        responses=None,
    )
    @action(detail=True, methods=["get"], url_path="unifi-ap")
    def unifi_ap(self, request, pk=None):
        """Current AP status (radios/health) + windowed InfluxDB time-series.

        Returns ``{status, timeseries}``; ``status`` is null when telemetry
        hasn't been collected for this AP yet (the tab renders an empty state).
        """
        from apps.integrations.serializers import UnifiApStatusSerializer
        from apps.integrations.unifi_telemetry import query_ap_timeseries

        device = self.get_object()
        status_obj = getattr(device, "unifi_ap_status", None)
        status_data = UnifiApStatusSerializer(status_obj).data if status_obj else None
        period = request.query_params.get("period", "1h")
        return Response({
            "status": status_data,
            "timeseries": query_ap_timeseries(str(device.id), period),
        })

    @extend_schema(
        summary="UniFi console/gateway telemetry: latest snapshot + time-series",
        parameters=[OpenApiParameter("period", str, description="1h|6h|24h|7d (default 1h)")],
        responses=None,
    )
    @action(detail=True, methods=["get"], url_path="unifi-console")
    def unifi_console(self, request, pk=None):
        """Current console status (controller health + WAN) + InfluxDB series.

        Returns ``{status, timeseries}``; ``status`` is null until telemetry has
        been collected for this console.
        """
        from apps.integrations.serializers import UnifiConsoleStatusSerializer
        from apps.integrations.unifi_telemetry import query_console_timeseries

        device = self.get_object()
        status_obj = getattr(device, "unifi_console_status", None)
        status_data = UnifiConsoleStatusSerializer(status_obj).data if status_obj else None
        period = request.query_params.get("period", "1h")
        return Response({
            "status": status_data,
            "timeseries": query_console_timeseries(str(device.id), period),
        })

    @action(detail=True, methods=["get"], url_path="reachability")
    def reachability(self, request, pk=None):
        """Ping/RTT latency + reachability history (?period=1h|6h|24h|7d)."""
        from . import metrics_influx
        device = self.get_object()
        return Response(metrics_influx.query_reachability(
            str(device.id), request.query_params.get("period", "1h")))

    @action(detail=False, methods=["get"], url_path="reachability-summary")
    def reachability_summary(self, request):
        """Fleet active/unreachable counts over time (?period=1h|6h|24h|7d)."""
        from . import metrics_influx
        return Response(metrics_influx.query_reachability_summary(
            request.query_params.get("period", "1h")))

    @action(detail=False, methods=["get"], url_path="ping-summary")
    def ping_summary(self, request):
        """Per-device ping current/avg/max/uptime + 24h sparkline for the device
        list. Cached 60s (same data for every user; the InfluxDB query is shared)."""
        from django.core.cache import cache
        from . import metrics_influx
        data = cache.get("ping_summary")
        if data is None:
            data = metrics_influx.query_ping_summary()
            cache.set("ping_summary", data, 60)
        return Response(data)

    @extend_schema(summary="Apply hostname rules to this device", request=None, responses=None)
    @action(detail=True, methods=["post"], url_path="apply-rules")
    def apply_rules(self, request, pk=None):
        """
        Apply matching hostname rules to assign role/site. By default only fills
        an unset role/site; pass {"force": true} to overwrite existing values.
        """
        from .hostname_rules import apply_hostname_rules
        device = self.get_object()
        role_assigned, site_assigned = apply_hostname_rules(
            device, force=_truthy(request.data.get("force")))
        device.refresh_from_db()
        return Response({
            "role_assigned": role_assigned,
            "site_assigned": site_assigned,
            "device": DeviceSerializer(device).data,
        })

    @extend_schema(summary="Apply hostname rules to all devices", request=None, responses=None)
    @action(detail=False, methods=["post"], url_path="apply-rules")
    def apply_rules_bulk(self, request):
        """
        Apply hostname rules across the fleet. By default only fills devices that
        are missing a role and/or site; pass {"force": true} to overwrite.
        """
        from .hostname_rules import apply_hostname_rules
        force = _truthy(request.data.get("force"))
        updated, skipped = 0, 0
        qs = Device.objects.select_related("role", "site").all()
        for device in qs:
            role_assigned, site_assigned = apply_hostname_rules(device, force=force)
            if role_assigned or site_assigned:
                updated += 1
            else:
                skipped += 1
        return Response({"updated": updated, "skipped": skipped})

    @action(detail=False, methods=["get"], url_path="platforms")
    def platforms(self, request):
        """
        Supported device platforms as [{value, label}] for UI dropdowns. Driven
        by Device.Platform, so adding a platform to the model surfaces it in the
        UI with no frontend change.
        """
        return Response([{"value": v, "label": label} for v, label in Device.Platform.choices])

    @staticmethod
    def _lldp_neighbors(device):
        from django.db.models import Q

        from .models import TopologyLink

        out = []
        links = (TopologyLink.objects
                 .filter(Q(device_a=device) | Q(device_b=device))
                 .select_related("device_a", "device_b"))
        for link in links:
            if link.device_a_id == device.id:
                neighbor, local_port, remote_port = link.device_b, link.port_a, link.port_b
            else:
                neighbor, local_port, remote_port = link.device_a, link.port_b, link.port_a
            out.append({
                "local_port": local_port,
                "neighbor_id": neighbor.id,
                "neighbor_hostname": neighbor.hostname,
                "remote_port": remote_port,
                "discovered_via": link.discovered_via,
            })
        return out

    @extend_schema(
        summary="How telemetry is currently being collected (gNMI streaming / SNMP polling)",
        responses=None,
    )
    @action(detail=True, methods=["get"], url_path="collection-status")
    def collection_status(self, request, pk=None):
        """
        Report whether gNMI streaming and/or SNMP polling are active for this
        device, based on recent InfluxDB telemetry writes, plus the configured
        intervals and SNMP version. Drives the collection-method badges on the
        device header and Telemetry tab.
        """
        from . import collection_status as cs
        device = self.get_object()
        return Response(cs.build_collection_status(device))

    @action(detail=True, methods=["get"], url_path="cve")
    def cve(self, request, pk=None):
        """CVE exposure for this device (same shape as /api/cve/device-cves/)."""
        from apps.cve.models import DeviceCVE
        from apps.cve.serializers import DeviceCVESerializer
        device = self.get_object()
        qs = DeviceCVE.objects.select_related("cve").filter(device=device)
        return Response(DeviceCVESerializer(qs, many=True).data)

    @extend_schema(summary="Weighted compliance score + findings for the device", responses=None)
    @action(detail=True, methods=["get"], url_path="compliance")
    def compliance(self, request, pk=None):
        """
        Weighted device compliance: template (50%) + interface rules (30%) +
        role consistency (20%), renormalised over the components that apply.

        Returns the overall ``score``/``grade``/``breakdown`` plus the detailed
        ``template_findings`` / ``interface_rule_findings`` /
        ``role_consistency_findings`` the Compliance tab renders. ``overall_score``
        (template-only average) and ``results`` are retained for back-compat.
        """
        from apps.compliance.device_score import run_and_store_compliance
        from apps.compliance.serializers import ComplianceTemplateResultSerializer
        device = self.get_object()

        # Scoring can reach live devices over REST (role-consistency checks); a
        # failure must not leak exception detail to the client (CodeQL). Storing
        # the weighted score here keeps the device list in sync with this tab.
        try:
            data = run_and_store_compliance(device)
        except Exception as exc:  # noqa: BLE001
            return internal_error_response(exc, logger, f"device compliance score {device.pk}")
        template_data = ComplianceTemplateResultSerializer(data["template_results"], many=True).data
        return Response({
            # Back-compat keys (template-only).
            "overall_score": data["template_score"],
            "results": template_data,
            # Weighted score + breakdown.
            "score": data["score"],
            "grade": data["grade"],
            "breakdown": data["breakdown"],
            # Detailed findings per component.
            "template_findings": template_data,
            "interface_rule_findings": data["interface_rule_findings"],
            "role_consistency_findings": data["role_consistency_findings"],
            "startup_status": data["startup_status"],
        })

    @action(detail=True, methods=["get"], url_path="audit")
    def audit(self, request, pk=None):
        """Recent audit events that target this device (most recent first)."""
        from apps.core.models import AuditLog
        from apps.core.serializers import AuditLogSerializer
        device = self.get_object()
        limit = min(int(request.query_params.get("limit", 10)), 100)
        rows = AuditLog.objects.filter(
            target_type="Device", target_id=str(device.id)
        ).select_related("user")[:limit]
        return Response(AuditLogSerializer(rows, many=True).data)

    @action(detail=True, methods=["get"], url_path="collection-log")
    def collection_log(self, request, pk=None):
        """Recent config-collection attempts for this device (most recent first)."""
        from apps.configbackup.models import ConfigCollectionLog
        from apps.configbackup.serializers import ConfigCollectionLogSerializer
        device = self.get_object()
        limit = min(int(request.query_params.get("limit", 25)), 200)
        rows = ConfigCollectionLog.objects.filter(device=device)[:limit]
        return Response(ConfigCollectionLogSerializer(rows, many=True).data)

    @extend_schema(summary="Re-run SNMP/SSH enrichment + interface/LLDP discovery", request=None, responses=None)
    @action(detail=True, methods=["post"], url_path="enrich")
    def enrich(self, request, pk=None):
        """
        Re-probe the device in the background to refresh model/OS/serial/platform
        and rediscover interfaces + LLDP links. Returns immediately (202); the
        device record updates in place.
        """
        from .enrich import trigger_enrich
        device = self.get_object()
        scheduled = trigger_enrich(device)
        return Response(
            {"status": "enrichment started" if scheduled else "enrichment unavailable",
             "device_id": device.id,
             "detail": None if scheduled else "No credential profile, or enrichment is disabled."},
            status=status.HTTP_202_ACCEPTED,
        )

    @extend_schema(
        summary="Re-verify the device's hostname (SNMP sysName / DNS) now",
        request=None,
        responses=inline_serializer("CheckHostnameResponse", {
            "hostname_changed": serializers.BooleanField(),
            "old_hostname": serializers.CharField(),
            "new_hostname": serializers.CharField(),
        }),
    )
    @action(detail=True, methods=["post"], url_path="check-hostname")
    def check_hostname(self, request, pk=None):
        """Synchronously re-check this device's hostname and update it if changed."""
        from .hostname_check import check_and_update_hostname
        device = self.get_object()
        return Response(check_and_update_hostname(device))

    @extend_schema(summary="Trigger an immediate SNMP poll of the device", request=None, responses=None)
    @action(detail=True, methods=["post"], url_path="poll-now")
    def poll_now(self, request, pk=None):
        """Republish the device config so the ingest-snmp poller polls it now."""
        from . import snmp_publish
        device = self.get_object()
        if snmp_publish.build_device_payload(device) is None:
            return Response(
                {"status": "not pollable", "device_id": device.id,
                 "detail": "device is inactive or has no SNMP credential profile"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        ok = snmp_publish.publish_poll_now(device)
        return Response({"status": "poll triggered" if ok else "publish failed",
                         "device_id": device.id})

    @extend_schema(request=None, responses=None)
    @action(detail=True, methods=["post"], url_path="topology/discover")
    def topology_discover(self, request, pk=None):
        """Discover this device's LLDP neighbors and persist matched links."""
        from . import topology as topo
        from apps.telemetry.discovery import DiscoveryError
        device = self.get_object()
        try:
            found = topo.discover_links(device)
        except DiscoveryError as exc:
            return Response({"error": safe_detail(exc, logger, "topology discover",
                            public="LLDP neighbor discovery failed."), "neighbors": []},
                            status=status.HTTP_502_BAD_GATEWAY)
        return Response({
            "count": len(found),
            "matched": sum(1 for f in found if f["matched_device_id"]),
            "neighbors": found,
        })

    @extend_schema(request=None, responses=None)
    @action(detail=True, methods=["post"], url_path="collect-lldp")
    def collect_lldp(self, request, pk=None):
        """Collect this device's LLDP neighbors now and persist them.

        Same scan as topology/discover, framed around neighbor persistence:
        returns the number of LLDPNeighbor rows refreshed for this device.
        """
        from apps.telemetry.discovery import DiscoveryError

        from . import topology as topo
        device = self.get_object()
        try:
            found = topo.discover_links(device)
        except DiscoveryError as exc:
            return Response({"error": safe_detail(exc, logger, "collect lldp",
                            public="LLDP collection failed."), "count": 0},
                            status=status.HTTP_502_BAD_GATEWAY)
        return Response({
            "device_id": device.id,
            "count": len(found),
            "matched": sum(1 for f in found if f["matched_device_id"]),
        })

    @action(detail=False, methods=["get"], url_path="topology")
    def topology(self, request):
        """
        Return nodes + edges for the network topology map. Edges come from
        discovered TopologyLink rows. Filters: site, role, device (center) + depth.
        """
        from collections import defaultdict
        from . import topology as topo
        from .models import LLDPNeighbor, TopologyLink

        params = request.query_params
        # ALL inventory devices belong on the map — including unreachable ones
        # (shown offline) and SNMP-only/newly-added devices with no LLDP links
        # (shown as isolated nodes). Decommissioned devices are excluded.
        devices = Device.objects.select_related("site", "role").filter(
            status__in=[
                Device.Status.ACTIVE, Device.Status.INACTIVE,
                Device.Status.MAINTENANCE, Device.Status.UNREACHABLE,
            ]
        )
        if params.get("site"):
            devices = devices.filter(site_id=params["site"])
        if params.get("role"):
            devices = devices.filter(notes__icontains=f"Role: {params['role']}")

        dev_ids = set(devices.values_list("id", flat=True))
        links = list(TopologyLink.objects.filter(device_a__in=dev_ids, device_b__in=dev_ids))
        # LLDP matches where the seen device AND its matched neighbour are both
        # in the inventory set — supplies edges for devices that don't report
        # LLDP themselves (e.g. APs seen by their uplink switch).
        neighbors = list(LLDPNeighbor.objects.filter(
            seen_by_id__in=dev_ids, matched_device_id__in=dev_ids))

        center = params.get("device")
        depth = params.get("depth")
        if center and str(center).isdigit() and int(center) in dev_ids:
            center = int(center)
            adj = defaultdict(set)
            for ln in links:
                adj[ln.device_a_id].add(ln.device_b_id)
                adj[ln.device_b_id].add(ln.device_a_id)
            for nb in neighbors:
                if nb.matched_device_id:
                    adj[nb.seen_by_id].add(nb.matched_device_id)
                    adj[nb.matched_device_id].add(nb.seen_by_id)
            max_depth = None if (not depth or depth == "all") else int(depth)
            visited, frontier, d = {center}, {center}, 0
            while frontier and (max_depth is None or d < max_depth):
                nxt = set()
                for n in frontier:
                    nxt |= {m for m in adj[n] if m not in visited}
                visited |= nxt
                frontier, d = nxt, d + 1
            dev_ids &= visited
            devices = devices.filter(id__in=dev_ids)
            links = [ln for ln in links if ln.device_a_id in dev_ids and ln.device_b_id in dev_ids]
            neighbors = [nb for nb in neighbors
                         if nb.seen_by_id in dev_ids and nb.matched_device_id in dev_ids]

        edges = topo.build_edges(links, neighbors, dev_ids)

        # Operator-defined manual links (devices without LLDP/CDP) — separate,
        # flagged edges the UI styles distinctly.
        manual = ManualTopologyLink.objects.filter(
            device_a__in=dev_ids, device_b__in=dev_ids)
        edges += topo.build_manual_edges(manual, dev_ids)

        # Degree (distinct neighbour count) per node, for the hover tooltip.
        degree: dict = defaultdict(int)
        for e in edges:
            degree[e["source"]] += 1
            degree[e["target"]] += 1

        # Latest AP telemetry for unifi_ap nodes (client count + radio summary).
        from apps.integrations.models import UnifiApStatus
        ap_status = {s.device_id: s for s in
                     UnifiApStatus.objects.filter(device_id__in=dev_ids)}

        def role_of(notes: str) -> str:
            for line in (notes or "").splitlines():
                if line.lower().startswith("role:"):
                    return line.split(":", 1)[1].strip()
            return ""

        nodes = []
        for d in devices:
            node = {
                "id": str(d.id), "label": d.hostname, "type": d.platform,
                "site": d.site.name if d.site else None, "status": d.status,
                # Role + colour: prefer the structured Role FK, fall back to the
                # legacy "Role:" notes convention.
                "role": d.role.name if d.role else role_of(d.notes),
                "role_slug": d.role.slug if d.role else "",
                "role_color": d.role.color if d.role else None,
                "risk_score": 0,
                "ip": str(d.ip_address or ""), "vendor": d.vendor or "",
                # Reachability + identity for offline styling and the tooltip.
                "is_reachable": d.is_reachable,
                "management_ip": str(d.management_ip) if d.management_ip else None,
                "model": d.model or "",
                "last_seen": d.last_seen.isoformat() if d.last_seen else None,
                "neighbor_count": degree.get(str(d.id), 0),
            }
            st = ap_status.get(d.id)
            if st:
                node["client_count"] = st.client_count
                node["radios"] = [
                    {"band": r.get("band"), "channel": r.get("channel")}
                    for r in (st.radios or [])
                ]
            nodes.append(node)
        return Response({"nodes": nodes, "edges": edges})

    @staticmethod
    def _csv_param(params, key):
        """Multi-value query param, accepting repeated keys and/or comma lists."""
        out = []
        for raw in params.getlist(key):
            out.extend(p.strip() for p in raw.split(",") if p.strip())
        return out

    def _undiscovered_lldp(self, request=None):
        """LLDPNeighbor rows that don't (currently) map to any inventory device.

        Re-checks live against the device index so a neighbor added since the
        last LLDP scan drops off, and surfaces neighbors whose stored
        matched_device was deleted. When `request` is given, applies the list
        filters (search / capabilities / exclude_capabilities / has_ip /
        platform); unmanaged capabilities are excluded by default unless an
        explicit `exclude_capabilities` param is sent. Returns
        (list[LLDPNeighbor], inventory_index).
        """
        from .lldp import (
            default_excluded_capabilities, device_identity_index,
            filter_undiscovered, neighbor_in_inventory,
        )

        devices = list(Device.objects.only("hostname", "ip_address", "management_ip"))
        idx = device_identity_index(devices)
        neighbors = (
            LLDPNeighbor.objects.select_related("seen_by")
            .order_by("seen_by__hostname", "local_interface")
        )
        undiscovered = [n for n in neighbors if not neighbor_in_inventory(n, idx[0], idx[1])]

        if request is not None:
            params = request.query_params
            # exclude_capabilities: present (even empty) overrides the default;
            # absent → fall back to the configured unmanaged set.
            if "exclude_capabilities" in params:
                exclude = self._csv_param(params, "exclude_capabilities")
            else:
                exclude = default_excluded_capabilities()
            has_ip_raw = (params.get("has_ip") or "").strip().lower()
            has_ip = {"true": True, "false": False}.get(has_ip_raw)
            undiscovered = filter_undiscovered(
                undiscovered,
                search=params.get("search", ""),
                include_caps=self._csv_param(params, "capabilities"),
                exclude_caps=exclude,
                has_ip=has_ip,
                platforms=self._csv_param(params, "platform"),
            )
        return undiscovered, idx

    @extend_schema(responses=LLDPNeighborSerializer(many=True))
    @action(detail=False, methods=["get"], url_path="lldp/undiscovered")
    def lldp_undiscovered(self, request):
        """LLDP neighbors seen by managed devices but not yet in inventory."""
        undiscovered, idx = self._undiscovered_lldp(request)
        data = LLDPNeighborSerializer(
            undiscovered, many=True, context={"inventory_index": idx}
        ).data
        return Response({"count": len(data), "results": data})

    @action(detail=False, methods=["get"], url_path="lldp/undiscovered/count")
    def lldp_undiscovered_count(self, request):
        """Just the count — drives the sidebar badge (cheap, no serialization).

        Honours the same filters (and the default unmanaged-capability
        exclusion) so the badge tracks the actionable, in-inventory-worthy set.
        """
        undiscovered, _ = self._undiscovered_lldp(request)
        return Response({"count": len(undiscovered)})


class HostnameRuleViewSet(CapabilityViewSetMixin, viewsets.ModelViewSet):
    """
    Manage hostname pattern rules that auto-assign device role and/or site
    during discovery approval, enrichment, and manual/bulk apply.

    Rules are evaluated in priority order (lowest number first); the first match
    per type wins. The `test/` action dry-runs a pattern against sample hostnames.
    """

    view_capability = "device:view"
    write_capability = "device:edit"

    queryset = HostnameRule.objects.select_related("role", "site").all()
    serializer_class = HostnameRuleSerializer
    filterset_fields = ["rule_type", "enabled"]
    search_fields = ["name", "pattern"]
    ordering_fields = ["priority", "name", "created_at"]
    ordering = ["priority", "name"]

    @extend_schema(
        request=HostnameRuleTestSerializer, responses=None,
        summary="Test a regex pattern against sample hostnames",
    )
    @action(detail=False, methods=["post"])
    def test(self, request):
        """Dry-run a pattern: returns [{hostname, matches}] for each sample."""
        import re

        req = HostnameRuleTestSerializer(data=request.data)
        req.is_valid(raise_exception=True)
        pattern = req.validated_data["pattern"]
        rx = re.compile(pattern, re.IGNORECASE)
        results = [
            {"hostname": h, "matches": bool(rx.search(h or ""))}
            for h in req.validated_data["hostnames"]
        ]
        return Response(results)

    @extend_schema(
        request=None, responses=None,
        summary="Dry-run bulk apply — what role/site each device would get (no save)",
    )
    @action(detail=False, methods=["post"])
    def preview(self, request):
        """
        Preview the bulk apply: returns the devices that would be updated (with
        current vs new role/site) and those that would be skipped (with a reason),
        without saving anything. {"force": true} mirrors the force apply.
        """
        from .hostname_rules import preview_hostname_rules
        return Response(preview_hostname_rules(force=_truthy(request.data.get("force"))))


# ── Discovery ─────────────────────────────────────────────────────────────────

_PLATFORM_VALUES = {p.value for p in Device.Platform}

# Methods the discovery engine actually executes (passive/import have no run).
_RUNNABLE_METHODS = (
    DiscoveryJob.Method.SCAN,
    DiscoveryJob.Method.PING_SNMP,
    DiscoveryJob.Method.PING,
    DiscoveryJob.Method.TOPOLOGY,
)


class DiscoveryJobViewSet(CapabilityViewSetMixin, viewsets.ModelViewSet):
    """
    Manage device discovery jobs (ping+SNMP / ping / topology / active scan /
    passive / import).

    Creating a job stores it in PENDING; the discovery engine (`run_discovery`)
    executes it and records DiscoveredDevice rows. Discovered devices always land
    in PENDING and require explicit approval — they are never auto-activated.
    Safety: `allowed_subnets` bound probing, `excluded_subnets` must list any
    OT/ICS ranges, `rate_limit_pps` defaults to 10.
    """

    view_capability = "device:view"
    write_capability = "device:edit"

    queryset = DiscoveryJob.objects.select_related("seed_device").order_by("-created_at")
    serializer_class = DiscoveryJobSerializer
    filterset_fields = ["method", "status"]
    search_fields = ["name"]

    def perform_create(self, serializer):
        user = self.request.user if self.request.user.is_authenticated else None
        job = serializer.save(created_by=user)
        from apps.core.audit import log_event
        from apps.core.models import AuditLog
        log_event(AuditLog.EventType.DISCOVERY_STARTED, request=self.request, target=job,
                  description=f"Discovery job '{job.name}' started",
                  metadata={"method": job.method, "subnets": job.subnets, "job_id": job.id})
        self._start_discovery(job)

    def update(self, request, *args, **kwargs):
        """Edit a job (PUT/PATCH) — blocked while it is running."""
        job = self.get_object()
        if job.status == DiscoveryJob.Status.RUNNING:
            return Response({"error": "Cannot edit a running job."},
                            status=status.HTTP_400_BAD_REQUEST)
        return super().update(request, *args, **kwargs)

    def _reset_and_start(self, job):
        """Reset a job to a fresh pending state and (re)trigger execution."""
        if job.status == DiscoveryJob.Status.RUNNING:
            return Response({"error": "Job is already running."},
                            status=status.HTTP_400_BAD_REQUEST)
        if job.method not in _RUNNABLE_METHODS:
            return Response({"error": "Only scan, ping_snmp, ping and topology jobs can be run."},
                            status=status.HTTP_400_BAD_REQUEST)
        job.status = DiscoveryJob.Status.PENDING
        job.progress_current = job.progress_total = job.ips_scanned = job.devices_found = 0
        job.progress_message = ""
        job.error_message = ""
        job.cancel_requested = False
        job.started_at = job.completed_at = None
        job.save()
        self._start_discovery(job)
        return Response(DiscoveryJobSerializer(job).data)

    @extend_schema(summary="Run a discovery job", request=None, responses=None)
    @action(detail=True, methods=["post"])
    def run(self, request, pk=None):
        """Reset progress and execute the job (scan/topology only)."""
        return self._reset_and_start(self.get_object())

    @extend_schema(summary="Restart a finished/cancelled discovery job", request=None, responses=None)
    @action(detail=True, methods=["post"])
    def restart(self, request, pk=None):
        """Reset a completed/failed/cancelled job and run it again."""
        return self._reset_and_start(self.get_object())

    @extend_schema(summary="Cancel a pending or running discovery job", request=None, responses=None)
    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        """
        Cancel a job. Pending jobs are cancelled immediately; running jobs get a
        cancel flag the engine polls and then stops (status → cancelled).
        """
        from django.utils import timezone

        job = self.get_object()
        if job.status not in (DiscoveryJob.Status.PENDING, DiscoveryJob.Status.RUNNING):
            return Response({"error": "Only pending or running jobs can be cancelled."},
                            status=status.HTTP_400_BAD_REQUEST)
        job.cancel_requested = True
        if job.status == DiscoveryJob.Status.PENDING:
            # Not started yet — cancel right away. (The flag also guards against a
            # worker thread that is just now picking the job up.)
            job.status = DiscoveryJob.Status.CANCELLED
            job.progress_message = "Cancelled by user"
            job.completed_at = timezone.now()
        job.save()
        return Response(DiscoveryJobSerializer(job).data)

    @staticmethod
    def _start_discovery(job):
        """
        Kick off execution for active-scan / topology jobs in a daemon thread so
        the POST returns immediately while the engine runs (status pending →
        running → completed). Passive/import jobs have no engine run.
        """
        from django.conf import settings as dj_settings
        from django.db import transaction

        if not getattr(dj_settings, "DISCOVERY_AUTORUN", True):
            return
        if job.method not in _RUNNABLE_METHODS:
            return
        from threading import Thread

        job_id = job.id
        # Start only after the job row is committed, so the worker thread's
        # separate DB connection can see it (runs immediately when not in an
        # atomic request).
        transaction.on_commit(
            lambda: Thread(target=DiscoveryJobViewSet._discovery_worker, args=(job_id,), daemon=True).start()
        )

    @staticmethod
    def _discovery_worker(job_id):
        """Thread entrypoint: run the job, then close this thread's DB connection."""
        from django.db import connection
        try:
            DiscoveryJobViewSet._run_discovery(job_id)
        finally:
            connection.close()

    @staticmethod
    def _run_discovery(job_id):
        from django.core.management import call_command

        try:
            # --job maps to the run_discovery command's `job` dest.
            call_command("run_discovery", job=job_id)
        except Exception as exc:  # noqa: BLE001 — record any failure on the job
            DiscoveryJob.objects.filter(id=job_id).update(
                status=DiscoveryJob.Status.FAILED,
                progress_message=str(exc)[:255],
                error_message=str(exc),
            )

    @action(detail=True, methods=["get"])
    def discovered(self, request, pk=None):
        """List devices discovered by this job (filter with ?status=pending)."""
        job = self.get_object()
        qs = job.discovered_devices.all().order_by("-confidence_score", "source_ip")
        status_filter = request.query_params.get("status")
        if status_filter:
            qs = qs.filter(status=status_filter)
        return Response(DiscoveredDeviceSerializer(qs, many=True).data)

    @extend_schema(summary="Live progress for a discovery job (poll while running)", responses=None)
    @action(detail=True, methods=["get"])
    def progress(self, request, pk=None):
        """Lightweight progress snapshot for polling during a running job."""
        from django.utils import timezone

        job = self.get_object()
        pct = round(min(job.progress_current / job.progress_total * 100, 100)) \
            if job.progress_total > 0 else 0
        if job.started_at:
            end = job.completed_at or timezone.now()
            elapsed = int((end - job.started_at).total_seconds())
        else:
            elapsed = 0
        return Response({
            "status": job.status,
            "progress_pct": pct,
            "progress_current": job.progress_current,
            "progress_total": job.progress_total,
            "progress_message": job.progress_message,
            "ips_scanned": job.ips_scanned,
            "devices_found": job.devices_found,
            "elapsed_seconds": elapsed,
            "error_message": job.error_message,
        })


class DiscoveredDeviceViewSet(CapabilityViewSetMixin, viewsets.ReadOnlyModelViewSet):
    """
    Inspect discovered devices and approve/reject them.

    Approval creates an ACTIVE Device from the fingerprint (never automatic).
    Rejection marks the candidate rejected. Filter by `status` or `job`.
    """

    view_capability = "device:view"
    write_capability = "device:edit"

    queryset = DiscoveredDevice.objects.select_related("job", "approved_device").all()
    serializer_class = DiscoveredDeviceSerializer
    filterset_fields = ["status", "job", "device_category"]
    ordering_fields = ["confidence_score", "created_at"]
    ordering = ["-confidence_score"]

    def get_queryset(self):
        qs = super().get_queryset()
        # When DISCOVERY_FILTER_ENDPOINTS is on, hide endpoint/workstation rows
        # from the list (they're still stored and reachable by id) unless the
        # caller passes ?show_all=true (or ?include_endpoints=true). Detail
        # routes and the explicit ?device_category= filter are never hidden.
        if self.action != "list":
            return qs
        from django.conf import settings
        if not getattr(settings, "DISCOVERY_FILTER_ENDPOINTS", True):
            return qs
        params = self.request.query_params
        if _truthy(params.get("show_all")) or _truthy(params.get("include_endpoints")):
            return qs
        if params.get("device_category"):  # explicit category filter wins
            return qs
        return qs.exclude(device_category=DiscoveredDevice.Category.ENDPOINT)

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        """Create an ACTIVE Device from this discovered device (idempotent-safe)."""
        from django.utils import timezone

        from .serializers import existing_device_for

        dd = self.get_object()

        # Already in inventory (already approved, or an existing device matches
        # this IP/hostname): resolve gracefully instead of erroring — link the
        # candidate to the existing device and return it for the UI to navigate.
        existing = existing_device_for(dd)
        if existing:
            if dd.status != DiscoveredDevice.Status.APPROVED:
                dd.status = DiscoveredDevice.Status.APPROVED
                dd.approved_device = existing
                dd.approved_by = request.user if request.user.is_authenticated else None
                dd.approved_at = timezone.now()
                dd.save(update_fields=["status", "approved_device", "approved_by",
                                       "approved_at", "updated_at"])
            return Response(
                {"device": DeviceSerializer(existing).data,
                 "discovered": DiscoveredDeviceSerializer(dd).data,
                 "already_exists": True},
                status=status.HTTP_200_OK,
            )

        hostname = dd.discovered_hostname or f"device-{dd.source_ip}"
        if Device.objects.filter(hostname=hostname).exists():
            hostname = f"{hostname}-{dd.source_ip}"
        # Platform: an explicit override from the request (used when discovery
        # couldn't identify it), else the discovered platform, else the known
        # vendor's default (fortinet → fortios, etc.), else "other".
        from .management.commands.run_discovery import default_platform_for_vendor
        platform = request.data.get("platform") or dd.discovered_platform
        if platform not in _PLATFORM_VALUES:
            platform = default_platform_for_vendor(dd.discovered_vendor) or Device.Platform.OTHER

        # Credentials to attach to the new device: explicit choice in the request,
        # else the job's credential profile (the creds used to discover it).
        cred_profile = dd.job.credential_profile
        cred_id = request.data.get("credential_profile")
        if cred_id not in (None, ""):
            cred_profile = CredentialProfile.objects.filter(pk=cred_id).first()
            if cred_profile is None:
                return Response(
                    {"error": f"Credential profile {cred_id} not found."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        device = Device.objects.create(
            hostname=hostname,
            ip_address=dd.source_ip,
            management_ip=dd.source_ip,
            vendor=dd.discovered_vendor or "",
            model=dd.discovered_model or "",
            platform=platform,
            os_version=dd.discovered_os or "",
            status=Device.Status.ACTIVE,
            credential_profile=cred_profile,
            # Inherit the discovery job's target site, if one was set.
            site=dd.job.site,
        )
        dd.status = DiscoveredDevice.Status.APPROVED
        dd.approved_device = device
        dd.approved_by = request.user if request.user.is_authenticated else None
        dd.approved_at = timezone.now()
        dd.save(update_fields=["status", "approved_device", "approved_by", "approved_at", "updated_at"])
        logger.info("Device created from discovery: %s (id=%s, ip=%s)",
                    device.hostname, device.id, device.ip_address)

        # Auto-assign role/site from hostname rules (won't override the job's site).
        from .hostname_rules import apply_hostname_rules
        apply_hostname_rules(device)

        # Inherit a site credential profile if none was set (now that role/site
        # are resolved). Never overrides an explicit/job credential.
        from apps.credentials.site_resolve import apply_site_credential
        apply_site_credential(device)

        # Enrich in the background (SNMP/SSH device info → interfaces → LLDP).
        from .enrich import trigger_enrich
        trigger_enrich(device)

        from apps.core.audit import log_event
        from apps.core.models import AuditLog
        log_event(AuditLog.EventType.DEVICE_APPROVED, request=request, target=device,
                  description=f"Device {device.hostname} approved from discovery job {dd.job_id}",
                  metadata={"job_id": dd.job_id, "source_ip": str(dd.source_ip)})

        return Response(
            {"device": DeviceSerializer(device).data,
             "discovered": DiscoveredDeviceSerializer(dd).data},
            status=status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        """Mark this discovered device rejected (no Device is created)."""
        dd = self.get_object()
        dd.status = DiscoveredDevice.Status.REJECTED
        dd.save(update_fields=["status", "updated_at"])
        from apps.core.audit import log_event
        from apps.core.models import AuditLog
        log_event(AuditLog.EventType.DEVICE_REJECTED, request=request, target=dd,
                  description=f"Discovered device {dd.source_ip} rejected")
        return Response(DiscoveredDeviceSerializer(dd).data)


class ManualTopologyLinkViewSet(CapabilityViewSetMixin, viewsets.ModelViewSet):
    """CRUD for operator-defined topology links (devices without LLDP/CDP).

    Filter by ``?device_id=`` (links touching a device) or ``?site_id=`` (links
    where either endpoint is at the site). Create/update/delete are audit-logged.
    """

    view_capability = "device:view"
    write_capability = "device:edit"

    queryset = ManualTopologyLink.objects.select_related(
        "device_a", "device_b", "created_by").all()
    serializer_class = ManualTopologyLinkSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        p = self.request.query_params
        did = p.get("device_id")
        if did:
            qs = qs.filter(Q(device_a_id=did) | Q(device_b_id=did))
        sid = p.get("site_id")
        if sid:
            qs = qs.filter(Q(device_a__site_id=sid) | Q(device_b__site_id=sid))
        return qs

    def perform_create(self, serializer):
        from apps.core.audit import log_event
        from apps.core.models import AuditLog
        link = serializer.save(created_by=self.request.user if self.request.user.is_authenticated else None)
        log_event(AuditLog.EventType.TOPOLOGY_LINK_CREATED, request=self.request,
                  description=f"Manual link {link}",
                  metadata={"link_id": link.id, "device_a": link.device_a_id,
                            "device_b": link.device_b_id, "link_type": link.link_type})

    def perform_update(self, serializer):
        from apps.core.audit import log_event
        from apps.core.models import AuditLog
        link = serializer.save()
        log_event(AuditLog.EventType.TOPOLOGY_LINK_UPDATED, request=self.request,
                  description=f"Manual link {link}", metadata={"link_id": link.id})

    def perform_destroy(self, instance):
        from apps.core.audit import log_event
        from apps.core.models import AuditLog
        desc = str(instance)
        link_id = instance.id
        instance.delete()
        log_event(AuditLog.EventType.TOPOLOGY_LINK_DELETED, request=self.request,
                  description=f"Manual link {desc}", metadata={"link_id": link_id})
