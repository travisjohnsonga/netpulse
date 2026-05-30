from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import generics, serializers, status
from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.credentials import vault
from apps.devices.models import Device

from . import config_gen, discovery
from .models import ConfigPush, MonitoredInterface, TelemetryConfig
from .serializers import (
    ConfigPushSerializer,
    DiscoveredInterfaceSerializer,
    GeneratedConfigSerializer,
    InterfaceBulkSaveSerializer,
    MonitoredInterfaceSerializer,
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
def metrics_stub(request):
    return Response({"detail": "Telemetry metrics API — not yet implemented."}, status=501)


class TelemetryConfigView(generics.RetrieveUpdateAPIView):
    """Get or update (auto-creating) the device's telemetry collection config."""

    serializer_class = TelemetryConfigSerializer

    def get_object(self):
        device = get_object_or_404(Device, pk=self.kwargs["device_id"])
        cfg, _ = TelemetryConfig.objects.get_or_create(device=device)
        return cfg


class DiscoverInterfacesView(APIView):
    """Discover interfaces on a device via SNMP or SSH (does not persist)."""

    @extend_schema(request=None, responses=DiscoveredInterfaceSerializer(many=True))
    def post(self, request, device_id):
        device = get_object_or_404(Device, pk=device_id)
        try:
            interfaces = discovery.discover_interfaces(device)
        except discovery.DiscoveryError as exc:
            return Response({"error": str(exc), "interfaces": []},
                            status=status.HTTP_502_BAD_GATEWAY)
        return Response({
            "count": len(interfaces),
            "auto_selected": sum(1 for i in interfaces if i.get("auto_select")),
            "interfaces": interfaces,
        })


class InterfaceListCreateView(APIView):
    """GET the device's monitored interfaces; POST to replace the selection."""

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
                last_discovered=now,
                last_status=it.get("oper_status") or "unknown",
            )
            for it in items
        ]
        MonitoredInterface.objects.bulk_create(created)
        qs = MonitoredInterface.objects.filter(device=device).order_by("if_name")
        return Response(MonitoredInterfaceSerializer(qs, many=True).data, status=status.HTTP_201_CREATED)


@extend_schema(responses={204: None})
class InterfaceDeleteView(APIView):
    """Remove a single interface from monitoring (if_name may contain slashes)."""

    def delete(self, request, device_id, if_name):
        obj = get_object_or_404(MonitoredInterface, device_id=device_id, if_name=if_name)
        obj.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema(responses=GeneratedConfigSerializer)
class GenerateConfigView(APIView):
    """Return platform-appropriate telemetry config snippets for a device."""

    def get(self, request, device_id):
        device = get_object_or_404(Device, pk=device_id)
        return Response(config_gen.generate(device))


class PushConfigView(APIView):
    """Push generated telemetry config to a device (POST); list push history (GET)."""

    @extend_schema(responses=ConfigPushSerializer(many=True))
    def get(self, request, device_id):
        get_object_or_404(Device, pk=device_id)
        qs = ConfigPush.objects.filter(device_id=device_id).order_by("-created_at")[:5]
        return Response(ConfigPushSerializer(qs, many=True).data)

    @extend_schema(request=PushRequestSerializer, responses=PushResponseSerializer)
    def post(self, request, device_id):
        device = get_object_or_404(Device, pk=device_id)
        req = PushRequestSerializer(data=request.data)
        req.is_valid(raise_exception=True)
        requested = req.validated_data["sections"]

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
            self._audit(device, request, requested, False, "", [f"connection failed: {exc}"])
            return Response({"success": False, "pushed_sections": [], "output": "",
                             "errors": [f"connection failed: {exc}"]},
                            status=status.HTTP_502_BAD_GATEWAY)

        try:
            for sec in requested:
                section = generated["sections"].get(sec)
                if not section or not section.get("config"):
                    errors.append(f"{sec}: no config available for this platform")
                    continue
                lines = config_gen.section_lines(section["config"])
                try:
                    out = conn.send_config_set(lines)
                    outputs.append(f"=== {sec} ===\n{out}")
                    pushed.append(sec)
                except Exception as exc:
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
