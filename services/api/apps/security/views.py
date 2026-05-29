from rest_framework.mixins import ListModelMixin, RetrieveModelMixin
from rest_framework.viewsets import GenericViewSet

from .models import DeviceRiskScore
from .serializers import DeviceRiskScoreSerializer


class DeviceRiskScoreViewSet(ListModelMixin, RetrieveModelMixin, GenericViewSet):
    queryset = DeviceRiskScore.objects.select_related("device").all()
    serializer_class = DeviceRiskScoreSerializer
    filterset_fields = ["device"]
    ordering_fields = ["score", "last_computed_at"]
