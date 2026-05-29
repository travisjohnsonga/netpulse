from rest_framework import viewsets
from rest_framework.mixins import ListModelMixin, RetrieveModelMixin, UpdateModelMixin
from rest_framework.viewsets import GenericViewSet

from .models import AlertChannel, AlertEvent, AlertRule
from .serializers import AlertChannelSerializer, AlertEventSerializer, AlertRuleSerializer


class AlertChannelViewSet(viewsets.ModelViewSet):
    """
    Manage alert delivery channels (Slack, email, PagerDuty, webhook).

    CRUD over the destinations alerts are routed to. Secret material (webhook
    URLs, routing keys) is referenced via OpenBao, not stored here. Filter by
    `channel_type` or `is_active`.
    """

    queryset = AlertChannel.objects.all()
    serializer_class = AlertChannelSerializer
    filterset_fields = ["channel_type", "is_active"]


class AlertRuleViewSet(viewsets.ModelViewSet):
    """
    Define alert rules — the conditions that generate alerts.

    Each rule carries a severity, a JSON `condition`, a cooldown and a set of
    delivery channels, and can be toggled with `is_active`. Filter by `severity`
    or `is_active`; search by name.
    """

    queryset = AlertRule.objects.prefetch_related("channels").all()
    serializer_class = AlertRuleSerializer
    filterset_fields = ["severity", "is_active"]
    search_fields = ["name"]


class AlertEventViewSet(ListModelMixin, RetrieveModelMixin, UpdateModelMixin, GenericViewSet):
    """
    Inspect and acknowledge fired alert events.

    Read-only listing plus PATCH to update state (e.g. acknowledge). Events carry
    device identity in their `labels` JSON. Filter by `rule`, `state` or
    `rule__severity`; order by created/resolved time.
    """

    queryset = AlertEvent.objects.select_related("rule").all()
    serializer_class = AlertEventSerializer
    filterset_fields = ["rule", "state", "rule__severity"]
    ordering_fields = ["created_at", "resolved_at"]
