from rest_framework.mixins import ListModelMixin, RetrieveModelMixin
from rest_framework.viewsets import GenericViewSet

from apps.core.permissions import HasCapability

from .models import DeviceRiskScore
from .serializers import DeviceRiskScoreSerializer


class DeviceRiskScoreViewSet(ListModelMixin, RetrieveModelMixin, GenericViewSet):
    permission_classes = [HasCapability("device:view")]
    queryset = DeviceRiskScore.objects.select_related("device").all()
    serializer_class = DeviceRiskScoreSerializer
    # Keyed by device id (/api/security/risk-scores/{device_id}/), since a
    # device has exactly one risk score and callers know the device, not the
    # risk-score row id.
    lookup_field = "device"
    filterset_fields = ["device"]
    ordering_fields = ["score", "last_computed_at"]
