from rest_framework import viewsets
from rest_framework.mixins import ListModelMixin, RetrieveModelMixin
from rest_framework.viewsets import GenericViewSet

from .models import CVE, DeviceCVE
from .serializers import CVESerializer, DeviceCVESerializer


class CVEViewSet(ListModelMixin, RetrieveModelMixin, GenericViewSet):
    queryset = CVE.objects.all()
    serializer_class = CVESerializer
    filterset_fields = ["severity"]
    search_fields = ["cve_id", "description"]
    ordering_fields = ["cvss_score", "published_at", "severity"]


class DeviceCVEViewSet(ListModelMixin, RetrieveModelMixin, GenericViewSet):
    queryset = DeviceCVE.objects.select_related("device", "cve").all()
    serializer_class = DeviceCVESerializer
    filterset_fields = ["device", "is_patched", "cve__severity"]
