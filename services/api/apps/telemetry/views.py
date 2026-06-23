from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import generics, serializers, status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import SAFE_METHODS
from rest_framework.response import Response
from rest_framework.views import APIView

import logging

from apps.core.errors import safe_detail
from apps.core.permissions import HasCapability
from apps.credentials import vault
from apps.devices.models import Device

from . import config_gen, discovery

logger = logging.getLogger(__name__)
from .models import ConfigPush, MonitoredInterface, SNMPGlobalSettings, TelemetryConfig
from .serializers import (
    ConfigPushSerializer,
    DiscoveredInterfaceSerializer,
    GeneratedConfigSerializer,
    InterfaceAlertConfigSerializer,
    InterfaceBulkSaveSerializer,
    MonitoredInterfaceSerializer,
    PollingSettingsSerializer,
    PushRequestSerializer,
    PushResponseSerializer,
    TelemetryConfigSerializer,
)


@extend_schema(
    summary="Telemetry metrics (not yet implemented)",
    description="Placeholder for the telemetry query API. Time-series data lives in "
                "InfluxDB; this endpoint returns 501 until the query layer ships.",
    responses={501: inline_serializer("NotImplemented", {"detail": serializers.CharField()})},
)
@api_view(["GET"])
@permission_classes([HasCapability("telemetry:view")])
def metrics_stub(request):
    return Response({"detail": "Telemetry metrics API — not yet implemented."}, status=501)


class TelemetryConfigView(generics.RetrieveUpdateAPIView):
    """Get or update (auto-creating) the device's telemetry collection config."""

    serializer_class = TelemetryConfigSerializer

    def get_permissions(self):
        if self.request.method in SAFE_METHODS:
            return [HasCapability("telemetry:view")()]
        return [HasCapability("telemetry:edit")()]

    def get_object(self):
        device = get_object_or_404(Device, pk=self.kwargs["device_id"])
        cfg, _ = TelemetryConfig.objects.get_or_create(device=device)
        return cfg

    def perform_update(self, serializer):
        import logging
        before = {f: getattr(serializer.instance, f) for f in
                  ("device_metrics_interval", "interface_traffic_interval",
                   "interface_status_interval", "bgp_interval", "override_intervals")}
        obj = serializer.save()
        after = {f: getattr(obj, f) for f in before}
        if before != after:
            user = getattr(self.request.user, "username", "?")
            logging.getLogger(__name__).info(
                "audit: %s changed SNMP polling intervals for %s — %s → %s",
                user, obj.device.hostname, before, after,
            )


class PollingSettingsView(generics.RetrieveUpdateAPIView):
    """Get or update the global SNMP polling intervals + session parameters."""

    serializer_class = PollingSettingsSerializer

    def get_permissions(self):
        if self.request.method in SAFE_METHODS:
            return [HasCapability("telemetry:view")()]
        return [HasCapability("telemetry:edit")()]

    def get_object(self):
        return SNMPGlobalSettings.load()


class DiscoverInterfacesView(APIView):
    """Discover interfaces on a device via SNMP or SSH (does not persist)."""

    permission_classes = [HasCapability("telemetry:edit")]

    @extend_schema(request=None, responses=DiscoveredInterfaceSerializer(many=True))
    def post(self, request, device_id):
        device = get_object_or_404(Device, pk=device_id)
        try:
            interfaces = discovery.discover_interfaces(device)
        except discovery.DiscoveryError as exc:
            return Response({"error": safe_detail(exc, logger, "discover interfaces",
                            public="Interface discovery failed."), "interfaces": []},
                            status=status.HTTP_502_BAD_GATEWAY)
        return Response({
            "count": len(interfaces),
            "auto_selected": sum(1 for i in interfaces if i.get("auto_select")),
            "interfaces": interfaces,
        })


class InterfaceListCreateView(APIView):
    """GET the device's monitored interfaces; POST to replace the selection."""

    def get_permissions(self):
        if self.request.method in SAFE_METHODS:
            return [HasCapability("telemetry:view")()]
        return [HasCapability("telemetry:edit")()]

    @extend_schema(responses=MonitoredInterfaceSerializer(many=True))
    def get(self, request, device_id):
        get_object_or_404(Device, pk=device_id)
        qs = MonitoredInterface.objects.filter(device_id=device_id).order_by("if_name")
        return Response(MonitoredInterfaceSerializer(qs, many=True).data)

    @extend_schema(request=InterfaceBulkSaveSerializer, responses=MonitoredInterfaceSerializer(many=True))
    def post(self, request, device_id):
        device = get_object_or_404(Device, pk=device_id)
        req = InterfaceBulkSaveSerializer(data=request.data)
        req.is_valid(raise_exception=True)
        items = req.validated_data["interfaces"]

        # Replace the whole selection for this device.
        before_names = set(
            MonitoredInterface.objects.filter(device=device).values_list("if_name", flat=True)
        )
        MonitoredInterface.objects.filter(device=device).delete()
        now = timezone.now()
        created = [
            MonitoredInterface(
                device=device,
                if_index=it.get("if_index"),
                if_name=it["if_name"],
                if_description=it.get("if_description", "") or "",
                if_speed_mbps=it.get("if_speed_mbps"),
                if_type=it.get("if_type", "") or "",
                lldp_neighbor_hostname=it.get("lldp_neighbor_hostname"),
                lldp_neighbor_port=it.get("lldp_neighbor_port"),
                lldp_neighbor_desc=it.get("lldp_neighbor_desc"),
                poll_traffic=it.get("poll_traffic", True),
                poll_errors=it.get("poll_errors", True),
                poll_status=it.get("poll_status", True),
                collection_method=it.get("collection_method", "auto"),
                alert_on_down=it.get("alert_on_down", True),
                alert_on_up=it.get("alert_on_up", True),
                alert_severity=it.get("alert_severity", "high"),
                consecutive_polls_before_alert=it.get("consecutive_polls_before_alert", 1),
                last_discovered=now,
                last_status=it.get("oper_status") or "unknown",
            )
            for it in items
        ]
        MonitoredInterface.objects.bulk_create(created)

        after_names = {it["if_name"] for it in items}
        added = sorted(after_names - before_names)
        removed = sorted(before_names - after_names)
        if added or removed:
            from apps.core.audit import log_event
            from apps.core.models import AuditLog
            log_event(
                AuditLog.EventType.DEVICE_UPDATED, request=request, target=device,
                description=(f"{device.hostname}: monitored interfaces updated — "
                             f"{len(added)} added, {len(removed)} removed"),
                metadata={"changes": [{
                    "field": "monitored_interfaces",
                    "label": "Monitored Interfaces",
                    "added": added,
                    "removed": removed,
                }]},
            )

        qs = MonitoredInterface.objects.filter(device=device).order_by("if_name")
        return Response(MonitoredInterfaceSerializer(qs, many=True).data, status=status.HTTP_201_CREATED)


@extend_schema(responses={204: None})
class InterfaceDeleteView(APIView):
    """Remove a single interface from monitoring (if_name may contain slashes)."""

    permission_classes = [HasCapability("telemetry:edit")]

    def delete(self, request, device_id, if_name):
        obj = get_object_or_404(MonitoredInterface, device_id=device_id, if_name=if_name)
        obj.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class InterfaceAlertConfigView(APIView):
    """Bulk-apply state-change alert settings to a device's interfaces (by name)."""

    permission_classes = [HasCapability("telemetry:edit")]

    @extend_schema(request=InterfaceAlertConfigSerializer, responses=MonitoredInterfaceSerializer(many=True))
    def post(self, request, device_id):
        get_object_or_404(Device, pk=device_id)
        req = InterfaceAlertConfigSerializer(data=request.data)
        req.is_valid(raise_exception=True)
        data = req.validated_data
        if_names = data.pop("if_names")
        updates = {k: v for k, v in data.items() if v is not None}
        qs = MonitoredInterface.objects.filter(device_id=device_id, if_name__in=if_names)
        if updates:
            qs.update(**updates)
        result = MonitoredInterface.objects.filter(device_id=device_id).order_by("if_name")
        return Response(MonitoredInterfaceSerializer(result, many=True).data)


@extend_schema(responses=GeneratedConfigSerializer)
class GenerateConfigView(APIView):
    """Return platform-appropriate telemetry config snippets for a device."""

    permission_classes = [HasCapability("telemetry:view")]

    def get(self, request, device_id):
        device = get_object_or_404(Device, pk=device_id)
        return Response(config_gen.generate(device))


class PushConfigView(APIView):
    """Push generated telemetry config to a device (POST); list push history (GET)."""

    def get_permissions(self):
        if self.request.method in SAFE_METHODS:
            return [HasCapability("telemetry:view")()]
        return [HasCapability("config:push")()]

    @extend_schema(responses=ConfigPushSerializer(many=True))
    def get(self, request, device_id):
        get_object_or_404(Device, pk=device_id)
        qs = ConfigPush.objects.filter(device_id=device_id).order_by("-created_at")[:5]
        return Response(ConfigPushSerializer(qs, many=True).data)

    @extend_schema(request=PushRequestSerializer, responses=PushResponseSerializer)
    def post(self, request, device_id):
        from django.conf import settings as dj_settings

        device = get_object_or_404(Device, pk=device_id)
        req = PushRequestSerializer(data=request.data)
        req.is_valid(raise_exception=True)
        requested = req.validated_data["sections"]

        # Master safety switch: when config push is disabled, block the push but
        # still audit the attempt so admins can see what would have been pushed.
        if not getattr(dj_settings, "ALLOW_CONFIG_PUSH", False):
            self._audit(device, request, [], False, "",
                        ["config push is disabled (ALLOW_CONFIG_PUSH=false)"])
            return Response(
                {"success": False, "pushed_sections": [], "output": "",
                 "errors": ["Config push is disabled. Set ALLOW_CONFIG_PUSH=true to enable."]},
                status=status.HTTP_403_FORBIDDEN,
            )

        profile = device.credential_profile
        if not profile or not profile.ssh_enabled:
            return Response({"success": False, "pushed_sections": [], "output": "",
                             "errors": ["device has no SSH credential profile"]},
                            status=status.HTTP_400_BAD_REQUEST)

        generated = config_gen.generate(device)
        creds = vault.read_secret(profile.vault_path) if profile.vault_path else {}

        pushed, errors, outputs = [], [], []
        try:
            from netmiko import ConnectHandler
            from apps.compliance.collector import netmiko_device_type
            dtype = netmiko_device_type(device.vendor, device.platform)
            if dtype == "autodetect":
                dtype = "cisco_ios"
            conn = ConnectHandler(
                device_type=dtype, host=str(device.management_ip or device.ip_address),
                username=profile.ssh_username, password=creds.get("ssh_password", ""),
                port=profile.ssh_port or 22, fast_cli=False,
            )
        except Exception as exc:
            logger.warning("telemetry push connect failed for %s: %s", device.hostname, exc, exc_info=True)
            # Audit record is internal-only; the HTTP response must not echo the
            # raw exception (it can carry connection/library internals).
            self._audit(device, request, requested, False, "", [f"connection failed: {exc}"])
            return Response({"success": False, "pushed_sections": [], "output": "",
                             "error": "SSH connection failed.",
                             "suggestion": "Check the device's SSH credentials, reachability and that SSH is enabled.",
                             "errors": ["connection failed"]},
                            status=status.HTTP_502_BAD_GATEWAY)

        try:
            for sec in requested:
                section = generated["sections"].get(sec)
                if not section or not section.get("config"):
                    errors.append(f"{sec}: no config available for this platform")
                    continue
                # Comment lines stripped + sanitised to ASCII; terminate on the
                # device prompt (Netmiko default) rather than a config comment.
                lines = config_gen.section_lines(section["config"])
                if not lines:
                    errors.append(f"{sec}: no pushable commands after stripping comments")
                    continue
                try:
                    out = conn.send_config_set(lines, read_timeout=30)
                    outputs.append(f"=== {sec} ===\n{out}")
                    pushed.append(sec)
                except Exception as exc:
                    import logging
                    logging.getLogger(__name__).warning("push %s on %s failed: %s", sec, device.hostname, exc)
                    errors.append(f"{sec}: {exc}")
        finally:
            try:
                conn.disconnect()
            except Exception:
                pass

        success = bool(pushed) and not errors
        output = "\n".join(outputs)
        self._audit(device, request, pushed, success, output, errors)
        return Response({"success": success, "pushed_sections": pushed, "output": output, "errors": errors})

    @staticmethod
    def _audit(device, request, sections, success, output, errors):
        import logging
        logging.getLogger(__name__).info(
            "telemetry config push: device=%s user=%s sections=%s success=%s",
            device.hostname, getattr(request.user, "username", "?"), sections, success,
        )
        ConfigPush.objects.create(
            device=device,
            pushed_by=request.user if request.user.is_authenticated else None,
            sections=sections, success=success, output=output, errors=errors,
        )
        # Mirror into the unified audit trail.
        from apps.core.audit import log_event
        from apps.core.models import AuditLog
        log_event(
            AuditLog.EventType.CONFIG_PUSHED, request=request, target=device,
            description=f"Configuration pushed to {device.hostname}",
            metadata={"sections": sections}, success=success,
            error_message="; ".join(errors)[:512] if errors else "",
        )
