import logging

from django.db.models import Prefetch
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.mixins import ListModelMixin, RetrieveModelMixin, UpdateModelMixin
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet

from apps.alerting.models import AlertAcknowledgement
from apps.core.permissions import CapabilityViewSetMixin

from .models import AlertChannel, AlertEvent, AlertRule, NotificationLog
from .serializers import (
    AlertChannelSerializer, AlertEventSerializer, AlertRuleSerializer, NotificationLogSerializer,
)

logger = logging.getLogger(__name__)


class AlertChannelViewSet(CapabilityViewSetMixin, viewsets.ModelViewSet):
    """
    Manage alert delivery channels (Slack, email, PagerDuty, webhook).

    CRUD over the destinations alerts are routed to. Secret material (webhook
    URLs, routing keys) is referenced via OpenBao, not stored here. Filter by
    `channel_type` or `is_active`.
    """

    view_capability = "alert:view"
    write_capability = "alert:manage"
    queryset = AlertChannel.objects.all()
    serializer_class = AlertChannelSerializer
    filterset_fields = ["channel_type", "is_active"]

    def perform_create(self, serializer):
        from .channel_secrets import store_channel_secrets
        channel = serializer.save()
        store_channel_secrets(channel)

    def perform_update(self, serializer):
        from .channel_secrets import store_channel_secrets
        channel = serializer.save()
        store_channel_secrets(channel)

    def perform_destroy(self, instance):
        from .channel_secrets import delete_channel_secrets
        delete_channel_secrets(instance.pk)
        instance.delete()

    @action(detail=True, methods=["post"], url_path="test")
    def test(self, request, pk=None):
        """Send a synthetic test notification through this one channel so an
        operator can verify it's wired (email arrives / Teams card posts)."""
        from django.conf import settings

        from .dispatch import send_to_channel
        from .payload import AlertPayload

        channel = self.get_object()
        base = (getattr(settings, "FRONTEND_BASE_URL", "") or "").rstrip("/")
        payload = AlertPayload(
            event_id=None, transition="firing", severity="info",
            title="spane test alert",
            message=f"This is a test notification from spane for channel '{channel.name}'.",
            rule_name="Channel Test",
            link=f"{base}/alerts" if base else "",
        )
        ok, detail = send_to_channel(channel, payload)
        return Response({"ok": ok, "detail": detail},
                        status=status.HTTP_200_OK if ok else status.HTTP_502_BAD_GATEWAY)


class AlertRuleViewSet(CapabilityViewSetMixin, viewsets.ModelViewSet):
    """
    Define alert rules — the conditions that generate alerts.

    Each rule carries a severity, a JSON `condition`, a cooldown and a set of
    delivery channels, and can be toggled with `is_active`. Filter by `severity`
    or `is_active`; search by name.
    """

    view_capability = "alert:view"
    write_capability = "alert:manage"
    queryset = AlertRule.objects.prefetch_related("channels").all()
    serializer_class = AlertRuleSerializer
    filterset_fields = ["severity", "is_active", "is_system", "kind"]
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
        rule = serializer.save(created_by=self.request.user)
        from apps.core.audit import log_event
        from apps.core.models import AuditLog
        log_event(AuditLog.EventType.ALERT_RULE_CREATED, request=self.request, target=rule,
                  description=f'Alert rule "{rule.name}" created')

    @staticmethod
    def _unique_copy_name(source_name: str, requested: str = "") -> str:
        """A distinct name for a clone. Never reuse the source name — an
        engine-fired built-in fires BY NAME, so a clone that reused it would get
        tangled with the engine's get_or_create. Start from ``requested`` (if
        given) or "{name} (copy)" and add a numeric suffix until it collides with
        no existing rule name."""
        base = (requested or "").strip() or f"{source_name} (copy)"
        candidate = base
        n = 2
        existing = set(AlertRule.objects.values_list("name", flat=True))
        while candidate in existing:
            candidate = f"{base} {n}"
            n += 1
        return candidate

    @action(detail=True, methods=["post"], url_path="clone")
    def clone(self, request, pk=None):
        """Copy any rule (system / built-in / user) into a NEW editable custom
        rule the caller owns — template off a built-in without touching the
        original. The clone is always kind=operational, is_system=False (fully
        deletable/editable, no engine recreation, no protection) and gets a
        DISTINCT non-engine name so it can't collide with an engine-fired rule."""
        source = self.get_object()
        name = self._unique_copy_name(source.name, request.data.get("name", ""))
        clone = AlertRule.objects.create(
            name=name,
            description=source.description,
            severity=source.severity,
            condition=source.condition,
            cooldown_minutes=source.cooldown_minutes,
            notify_enabled=source.notify_enabled,
            # A clone is always a pristine user-owned operational rule.
            kind=AlertRule.Kind.OPERATIONAL,
            is_system=False,
            is_active=True,
            created_by=request.user,
        )
        clone.channels.set(source.channels.all())
        from apps.core.audit import log_event
        from apps.core.models import AuditLog
        log_event(AuditLog.EventType.ALERT_RULE_CREATED, request=request, target=clone,
                  description=f'Alert rule "{clone.name}" cloned from "{source.name}"')
        serializer = self.get_serializer(clone)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

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
        """Three-category protection (rule-management arc):
          • Tier-1 SYSTEM rules (kind=system) — spane's own health/machinery,
            never deletable; disable (with a warning) instead.
          • Engine-fired built-ins (is_system=True) — deleting is futile: the
            engine re-creates the rule by name on the next event. Not deletable;
            free disable actually stops the alerts (the engine finds the disabled
            row and skips firing — see apps/alerts/gating.py).
          • Pure user-created rules — deletable; seed-once keeps the deletion
            from resurrecting on the next reboot."""
        rule = self.get_object()
        if rule.kind == AlertRule.Kind.SYSTEM:
            return Response(
                {"error": "System rules monitor spane's own health and cannot be "
                          "deleted. Disable the rule instead."},
                status=status.HTTP_403_FORBIDDEN,
            )
        if rule.is_system:
            return Response(
                {"error": "Built-in monitoring rules are re-created automatically by "
                          "spane's engines. Disable it instead to stop its alerts."},
                status=status.HTTP_403_FORBIDDEN,
            )
        name = rule.name
        from apps.core.audit import log_event
        from apps.core.models import AuditLog
        log_event(AuditLog.EventType.ALERT_RULE_DELETED, request=request, target=rule,
                  description=f'Alert rule "{name}" deleted')
        return super().destroy(request, *args, **kwargs)


class AlertEventViewSet(CapabilityViewSetMixin, ListModelMixin, RetrieveModelMixin, UpdateModelMixin, GenericViewSet):
    """
    Inspect and acknowledge fired alert events.

    Read-only listing plus PATCH to update state (e.g. acknowledge). Events carry
    device identity in their `labels` JSON. Filter by `rule`, `state` or
    `rule__severity`; order by created/resolved time.
    """

    view_capability = "alert:view"
    write_capability = "alert:manage"
    queryset = AlertEvent.objects.select_related("rule").prefetch_related(
        Prefetch("acknowledgements",
                 queryset=AlertAcknowledgement.objects.select_related("acknowledged_by")
                 .order_by("-acknowledged_at"))).all()
    serializer_class = AlertEventSerializer
    # `state` is handled in get_queryset (it has a derived "acknowledged" value
    # the model enum doesn't), so it's intentionally NOT a filterset field —
    # DjangoFilterBackend would 400 on state=acknowledged.
    filterset_fields = ["rule", "rule__severity"]
    ordering_fields = ["created_at", "resolved_at"]

    def get_queryset(self):
        """
        Default to active (firing) alerts only. ?resolved=true → resolved only;
        ?resolved=all → no filter. ?state=acknowledged → firing events that have
        an acknowledgement. An explicit ?state= filter takes precedence.
        """
        qs = super().get_queryset()
        # Only the list view defaults to active-only; retrieve/actions must still
        # reach resolved events by pk.
        if self.action != "list":
            return qs
        params = self.request.query_params
        state = params.get("state")
        if state == "acknowledged":
            # Derived state: firing + has an ack (the model has no ACK state).
            return qs.filter(state=AlertEvent.State.FIRING, acknowledgements__isnull=False).distinct()
        if state == "firing":
            # Firing AND not yet acknowledged (the Firing tab = needs attention).
            return qs.filter(state=AlertEvent.State.FIRING, acknowledgements__isnull=True)
        if state:
            return qs.filter(state=state)  # e.g. ?state=resolved
        resolved = params.get("resolved")
        if resolved == "all":
            return qs
        if resolved == "true":
            return qs.filter(state=AlertEvent.State.RESOLVED)
        return qs.exclude(state=AlertEvent.State.RESOLVED)

    @action(detail=False, methods=["get"], url_path="summary")
    def summary(self, request):
        """State counts for the Alerts filter tabs: all/firing/acknowledged/resolved."""
        base = AlertEvent.objects.all()
        firing = base.filter(state=AlertEvent.State.FIRING)
        acked = firing.filter(acknowledgements__isnull=False).distinct().count()
        firing_open = firing.filter(acknowledgements__isnull=True).count()
        resolved = base.filter(state=AlertEvent.State.RESOLVED).count()
        return Response({
            "all": firing_open + acked + resolved,
            "firing": firing_open,
            "acknowledged": acked,
            "resolved": resolved,
        })

    @action(detail=False, methods=["post"], url_path="bulk-acknowledge")
    def bulk_acknowledge(self, request):
        """Acknowledge many alerts at once (stops escalation on each)."""
        ids = request.data.get("ids") or []
        note = request.data.get("note", "") or ""
        if not ids:
            return Response({"error": "No IDs provided"}, status=status.HTTP_400_BAD_REQUEST)
        # Only firing (unresolved) events can be acknowledged.
        events = AlertEvent.objects.filter(id__in=ids, state=AlertEvent.State.FIRING)
        updated, errors = 0, []
        for event in events:
            try:
                self._record_ack(event, request.user, note, None)
                updated += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning("bulk ack failed for event %s: %s", event.id, exc)
                errors.append({"id": event.id, "error": "acknowledge failed"})
        return Response({"updated": updated, "failed": len(ids) - updated, "errors": errors})

    @action(detail=False, methods=["post"], url_path="bulk-resolve")
    def bulk_resolve(self, request):
        """Resolve many alerts at once (only those not already resolved)."""
        from django.utils import timezone
        ids = request.data.get("ids") or []
        note = request.data.get("resolution_note", "") or request.data.get("note", "") or ""
        if not ids:
            return Response({"error": "No IDs provided"}, status=status.HTTP_400_BAD_REQUEST)
        target = (AlertEvent.objects
                  .filter(id__in=ids).exclude(state=AlertEvent.State.RESOLVED))
        pks = list(target.values_list("pk", flat=True))
        updated = target.update(state=AlertEvent.State.RESOLVED,
                                resolved_at=timezone.now(), resolved_by="user",
                                resolution_note=note, updated_at=timezone.now())
        if pks:
            # .update() bypasses the post_save dispatch signal → notify explicitly.
            from .resolve import notify_resolved
            notify_resolved(pks)
        return Response({"updated": updated, "failed": len(ids) - updated, "errors": []})

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


class NotificationLogViewSet(CapabilityViewSetMixin, ListModelMixin, GenericViewSet):
    """
    Notification delivery log + delivery-health — the source of truth for "did the
    alert actually get delivered." Dispatch writes a row per attempt (sent/failed)
    so a silent send failure is visible here, in `delivery-health/`, and (when
    persistent) via a cross-channel meta-alarm.

    `GET /api/alerts/notifications/` — recent deliveries (filter `status`,
    `channel`, `channel_type`). `GET /api/alerts/notifications/delivery-health/` —
    per-channel health summary (last success/failure, currently-failing).
    """

    view_capability = "alert:view"
    write_capability = "alert:manage"
    queryset = NotificationLog.objects.select_related("event", "event__rule", "channel").all()
    serializer_class = NotificationLogSerializer
    filterset_fields = ["status", "channel", "channel_type"]
    ordering = ["-created_at"]

    @action(detail=False, methods=["get"], url_path="delivery-health")
    def delivery_health(self, request):
        from .delivery_health import delivery_health
        try:
            window = int(request.query_params.get("window_minutes", 60))
        except (TypeError, ValueError):
            window = 60
        return Response(delivery_health(window_minutes=max(1, min(window, 1440))))
