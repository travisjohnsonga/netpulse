from rest_framework import viewsets

from apps.core.permissions import CapabilityViewSetMixin

from .models import LifecycleMilestone
from .serializers import LifecycleMilestoneSerializer


class LifecycleMilestoneViewSet(CapabilityViewSetMixin, viewsets.ModelViewSet):
    view_capability = "lifecycle:view"
    write_capability = "lifecycle:edit"
    queryset = LifecycleMilestone.objects.select_related("device").all()
    serializer_class = LifecycleMilestoneSerializer
    filterset_fields = ["device", "milestone_type"]
    ordering_fields = ["milestone_date", "milestone_type"]
