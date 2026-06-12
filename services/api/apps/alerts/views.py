from rest_framework import status, viewsets
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
    filterset_fields = ["severity", "is_active", "is_system"]
    search_fields = ["name"]

    # Labels for the field-level diff surfaced in the audit log.
    _AUDIT_LABELS = {
        "name": "Name", "severity": "Severity", "is_active": "Active",
        "cooldown_minutes": "Cooldown (min)", "condition": "Condition",
    }

    @staticmethod
    def _snapshot(rule):
        return {
            "name": rule.name,
            "severity": rule.severity,
            "is_active": rule.is_active,
            "cooldown_minutes": rule.cooldown_minutes,
            "condition": rule.condition,
        }

    def perform_create(self, serializer):
        rule = serializer.save()
        from apps.core.audit import log_event
        from apps.core.models import AuditLog
        log_event(AuditLog.EventType.ALERT_RULE_CREATED, request=self.request, target=rule,
                  description=f'Alert rule "{rule.name}" created')

    def update(self, request, *args, **kwargs):
        from apps.core.audit import describe_changes, diff_model_changes, log_event
        from apps.core.models import AuditLog
        rule = self.get_object()
        before = self._snapshot(rule)
        response = super().update(request, *args, **kwargs)
        rule.refresh_from_db()
        changes = diff_model_changes(before, self._snapshot(rule), self._AUDIT_LABELS)
        if changes:
            log_event(AuditLog.EventType.ALERT_RULE_UPDATED, request=request, target=rule,
                      description=describe_changes(f'Alert rule "{rule.name}"', changes),
                      metadata={"changes": changes})
        return response

    def destroy(self, request, *args, **kwargs):
        """System rules are protected — disable (is_active=False) them instead."""
        rule = self.get_object()
        if rule.is_system:
            return Response(
                {"error": "System rules cannot be deleted. Disable the rule instead."},
                status=status.HTTP_403_FORBIDDEN,
            )
        name = rule.name
        from apps.core.audit import log_event
        from apps.core.models import AuditLog
        log_event(AuditLog.EventType.ALERT_RULE_DELETED, request=request, target=rule,
                  description=f'Alert rule "{name}" deleted')
        return super().destroy(request, *args, **kwargs)


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

    def get_queryset(self):
        """
        Default to active (firing) alerts only. ?resolved=true → resolved only;
        ?resolved=all → no filter. An explicit ?state= filter takes precedence.
        """
        qs = super().get_queryset()
        # Only the list view defaults to active-only; retrieve/actions must still
        # reach resolved events by pk.
        if self.action != "list":
            return qs
        params = self.request.query_params
        if params.get("state"):
            return qs  # explicit state filter handled by django-filter
        resolved = params.get("resolved")
        if resolved == "all":
            return qs
        if resolved == "true":
            return qs.filter(state=AlertEvent.State.RESOLVED)
        return qs.exclude(state=AlertEvent.State.RESOLVED)

    @action(detail=True, methods=["post"])
    def resolve(self, request, pk=None):
        """Manually resolve an alert."""
        from django.utils import timezone
        event = self.get_object()
        event.state = AlertEvent.State.RESOLVED
        event.resolved_at = timezone.now()
        event.resolved_by = "user"
        event.resolution_note = request.data.get("note", "") or ""
        event.save(update_fields=["state", "resolved_at", "resolved_by", "resolution_note", "updated_at"])
        return Response(self.get_serializer(event).data)

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
