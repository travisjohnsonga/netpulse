from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.mixins import ListModelMixin, RetrieveModelMixin, UpdateModelMixin
from rest_framework.response import Response
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

    def _record_ack(self, event, user, note, snooze_minutes):
        """Create an acknowledgement, stop escalation (cancel pending sends)."""
        from datetime import timedelta

        from django.utils import timezone

        from apps.alerting.models import AlertAcknowledgement, AlertNotification

        now = timezone.now()
        snoozed = now + timedelta(minutes=int(snooze_minutes)) if snooze_minutes else None
        ack = AlertAcknowledgement.objects.create(
            alert_event=event, acknowledged_by=user, acknowledged_at=now,
            note=note or "", snoozed_until=snoozed,
        )
        # Stop escalation: pending notifications for this event become cancelled.
        AlertNotification.objects.filter(
            alert_event=event, status=AlertNotification.Status.PENDING,
        ).update(status=AlertNotification.Status.CANCELLED)
        return ack, snoozed

    @action(detail=True, methods=["post"])
    def acknowledge(self, request, pk=None):
        """Acknowledge an alert (optionally snooze): stops escalation."""
        from apps.alerting.serializers import AlertAcknowledgementSerializer
        event = self.get_object()
        ack, _ = self._record_ack(
            event, request.user, request.data.get("note"), request.data.get("snooze_minutes"))
        return Response(AlertAcknowledgementSerializer(ack).data)

    @action(detail=True, methods=["post"])
    def snooze(self, request, pk=None):
        """Snooze an alert for N minutes (re-escalates afterwards)."""
        from apps.alerting.serializers import AlertAcknowledgementSerializer
        event = self.get_object()
        minutes = request.data.get("minutes") or request.data.get("snooze_minutes") or 30
        ack, _ = self._record_ack(event, request.user, request.data.get("note"), minutes)
        return Response(AlertAcknowledgementSerializer(ack).data)
