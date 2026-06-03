from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.devices.serializers import DeviceListSerializer

from .models import Collector
from .serializers import CollectorSerializer


class CollectorViewSet(viewsets.ModelViewSet):
    queryset = Collector.objects.all()
    serializer_class = CollectorSerializer
    filterset_fields = ["status", "collector_type"]
    search_fields = ["name", "remote_ip", "hostname"]
    ordering_fields = ["last_seen_at", "created_at", "status", "collector_type"]

    @action(detail=True, methods=["get"], url_path="devices")
    def devices(self, request, pk=None):
        """List devices explicitly assigned to this collector."""
        collector = self.get_object()
        return Response(DeviceListSerializer(collector.devices.all(), many=True).data)
