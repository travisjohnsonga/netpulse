from rest_framework import viewsets

from .models import LifecycleMilestone
from .serializers import LifecycleMilestoneSerializer


class LifecycleMilestoneViewSet(viewsets.ModelViewSet):
    queryset = LifecycleMilestone.objects.select_related("device").all()
    serializer_class = LifecycleMilestoneSerializer
    filterset_fields = ["device", "milestone_type"]
    ordering_fields = ["milestone_date", "milestone_type"]
