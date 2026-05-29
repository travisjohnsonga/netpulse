from drf_spectacular.utils import extend_schema
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from . import fingerprint
from .models import Device, DeviceGroup, Site
from .serializers import (
    DeviceGroupSerializer,
    DeviceListSerializer,
    DeviceSerializer,
    SiteSerializer,
    TestConnectionRequestSerializer,
    TestConnectionResponseSerializer,
)


class SiteViewSet(viewsets.ModelViewSet):
    queryset = Site.objects.all()
    serializer_class = SiteSerializer


class DeviceGroupViewSet(viewsets.ModelViewSet):
    queryset = DeviceGroup.objects.all()
    serializer_class = DeviceGroupSerializer


class DeviceViewSet(viewsets.ModelViewSet):
    """
    Manage network devices — the core inventory of NetPulse.

    Full CRUD over devices (routers, switches, firewalls, etc.). List responses
    use a lightweight serializer; retrieve returns the full record including site,
    groups and associated credential profiles. Filter by `status`, `platform`,
    `vendor` or `site`; search across hostname, IP and serial number. The
    `topology/` action returns nodes + edges for the network map.
    """

    queryset = Device.objects.select_related("site").prefetch_related("groups").all()
    filterset_fields = ["status", "platform", "vendor", "site"]
    search_fields = ["hostname", "ip_address", "serial_number"]
    ordering_fields = ["hostname", "status", "created_at"]

    def get_serializer_class(self):
        if self.action == "list":
            return DeviceListSerializer
        return DeviceSerializer

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
        result = fingerprint.fingerprint(req.validated_data["ip"])
        return Response(TestConnectionResponseSerializer(result).data)

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
