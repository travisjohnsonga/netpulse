from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import generics, serializers, status
from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.devices.models import Device

from . import discovery
from .models import MonitoredInterface, TelemetryConfig
from .serializers import (
    DiscoveredInterfaceSerializer,
    InterfaceBulkSaveSerializer,
    MonitoredInterfaceSerializer,
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
