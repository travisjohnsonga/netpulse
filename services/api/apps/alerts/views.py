from rest_framework import viewsets
from rest_framework.mixins import ListModelMixin, RetrieveModelMixin, UpdateModelMixin
from rest_framework.viewsets import GenericViewSet

from .models import AlertChannel, AlertEvent, AlertRule
from .serializers import AlertChannelSerializer, AlertEventSerializer, AlertRuleSerializer


class AlertChannelViewSet(viewsets.ModelViewSet):
    queryset = AlertChannel.objects.all()
    serializer_class = AlertChannelSerializer
    filterset_fields = ["channel_type", "is_active"]


class AlertRuleViewSet(viewsets.ModelViewSet):
    queryset = AlertRule.objects.prefetch_related("channels").all()
    serializer_class = AlertRuleSerializer
    filterset_fields = ["severity", "is_active"]
    search_fields = ["name"]


class AlertEventViewSet(ListModelMixin, RetrieveModelMixin, UpdateModelMixin, GenericViewSet):
    queryset = AlertEvent.objects.select_related("rule").all()
    serializer_class = AlertEventSerializer
    filterset_fields = ["rule", "state", "rule__severity"]
    ordering_fields = ["created_at", "resolved_at"]
