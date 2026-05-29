from rest_framework import viewsets

from .models import Collector
from .serializers import CollectorSerializer


class CollectorViewSet(viewsets.ModelViewSet):
    queryset = Collector.objects.all()
    serializer_class = CollectorSerializer
    filterset_fields = ["status"]
    search_fields = ["name", "remote_ip"]
    ordering_fields = ["last_seen_at", "created_at", "status"]
