from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from .models import Device, DeviceGroup, Site
from .serializers import DeviceGroupSerializer, DeviceListSerializer, DeviceSerializer, SiteSerializer


class SiteViewSet(viewsets.ModelViewSet):
    queryset = Site.objects.all()
    serializer_class = SiteSerializer


class DeviceGroupViewSet(viewsets.ModelViewSet):
    queryset = DeviceGroup.objects.all()
    serializer_class = DeviceGroupSerializer


class DeviceViewSet(viewsets.ModelViewSet):
    queryset = Device.objects.select_related("site").prefetch_related("groups").all()
    filterset_fields = ["status", "platform", "vendor", "site"]
    search_fields = ["hostname", "ip_address", "serial_number"]
    ordering_fields = ["hostname", "status", "created_at"]

    def get_serializer_class(self):
        if self.action == "list":
            return DeviceListSerializer
        return DeviceSerializer

    @action(detail=False, methods=["get"], url_path="topology")
    def topology(self, request):
        """
        Return nodes + edges for the network topology map.
        Topology links are populated via CDP/LLDP discovery (future).
        Returns empty edges until the topology_links table is populated.
        """
        devices = Device.objects.select_related("site").filter(
            status__in=[Device.Status.ACTIVE, Device.Status.INACTIVE, Device.Status.MAINTENANCE]
        )
        nodes = [
            {
                "id": str(d.id),
                "label": d.hostname,
                "type": d.platform,
                "site": d.site.name if d.site else None,
                "status": d.status,
                "risk_score": 0,
            }
            for d in devices
        ]
        return Response({"nodes": nodes, "edges": []})
