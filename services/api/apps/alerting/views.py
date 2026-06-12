from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.mixins import ListModelMixin, RetrieveModelMixin
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet

from . import engine
from .models import (
    AlertNotification, AlertRoute, ContactMethod, EscalationPolicy,
    EscalationStep, MaintenanceWindow, OnCallSchedule, OnCallShift, Team, TeamMember,
)
from .serializers import (
    AlertNotificationSerializer, AlertRouteSerializer, ContactMethodSerializer,
    EscalationPolicySerializer, EscalationStepSerializer, MaintenanceWindowSerializer,
    OnCallScheduleSerializer, OnCallShiftSerializer, TeamMemberSerializer, TeamSerializer,
)


class TeamViewSet(viewsets.ModelViewSet):
    """Alerting teams + their membership."""

    queryset = Team.objects.all()
    serializer_class = TeamSerializer
    search_fields = ["name"]
    ordering_fields = ["name", "created_at"]
    ordering = ["name"]

    @action(detail=True, methods=["get", "post"])
    def members(self, request, pk=None):
        team = self.get_object()
        if request.method == "GET":
            qs = team.memberships.select_related("user").all()
            return Response(TeamMemberSerializer(qs, many=True).data)
        ser = TeamMemberSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        ser.save(team=team)
        return Response(ser.data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"], url_path="test-discord")
    def test_discord(self, request, pk=None):
        """Send a test embed to the team's Discord webhook."""
        from . import channels
        team = self.get_object()
        if not team.discord_webhook_url:
            return Response({"ok": False, "error": "no Discord webhook configured"},
                            status=status.HTTP_400_BAD_REQUEST)
        payload = channels.discord_embed(
            "spane Test Notification",
            "Discord alerting is configured correctly.", "info")
        ok, err = channels.send_discord(team.discord_webhook_url, payload)
        return Response({"ok": ok, "error": err})

    @action(detail=True, methods=["delete", "patch"], url_path=r"members/(?P<user_id>[^/.]+)")
    def member_detail(self, request, pk=None, user_id=None):
        """PATCH role / notify_* for a member, or DELETE them from the team."""
        team = self.get_object()
        membership = team.memberships.filter(user_id=user_id).select_related("user").first()
        if membership is None:
            return Response({"detail": "not a member"}, status=status.HTTP_404_NOT_FOUND)
        if request.method == "DELETE":
            membership.delete()
            return Response(status=status.HTTP_204_NO_CONTENT)
        ser = TeamMemberSerializer(membership, data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        ser.save()
        return Response(ser.data)


class ContactMethodViewSet(viewsets.ModelViewSet):
    """Per-user contact methods (email/sms/slack/…)."""

    queryset = ContactMethod.objects.select_related("user").all()
    serializer_class = ContactMethodSerializer
    filterset_fields = ["user", "type", "is_primary"]


class EscalationPolicyViewSet(viewsets.ModelViewSet):
    """Escalation policies and their ordered steps."""

    queryset = EscalationPolicy.objects.select_related("team").prefetch_related("steps").all()
    serializer_class = EscalationPolicySerializer
    filterset_fields = ["team"]
    search_fields = ["name"]

    @action(detail=True, methods=["get", "post"])
    def steps(self, request, pk=None):
        policy = self.get_object()
        if request.method == "GET":
            return Response(EscalationStepSerializer(policy.steps.all(), many=True).data)
        ser = EscalationStepSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        ser.save(policy=policy)
        return Response(ser.data, status=status.HTTP_201_CREATED)


class AlertRouteViewSet(viewsets.ModelViewSet):
    """Alert routing rules (priority-ordered)."""

    queryset = AlertRoute.objects.select_related("escalation_policy").prefetch_related("match_sites").all()
    serializer_class = AlertRouteSerializer
    filterset_fields = ["is_active", "escalation_policy"]
    search_fields = ["name"]
    ordering_fields = ["priority", "name"]
    ordering = ["priority"]

    @action(detail=False, methods=["post"])
    def test(self, request):
        """Match a sample alert against the routes; returns the winning route."""
        d = request.data
        route = engine.find_matching_route(
            severity=d.get("severity"), source=d.get("source"),
            check_type=d.get("check_type"), site_id=d.get("site"),
        )
        return Response({
            "matched": route is not None,
            "route": AlertRouteSerializer(route).data if route else None,
        })


class EscalationStepViewSet(viewsets.ModelViewSet):
    """Direct CRUD on escalation steps (also editable via the policy action)."""

    queryset = EscalationStep.objects.all()
    serializer_class = EscalationStepSerializer
    filterset_fields = ["policy"]
    ordering = ["policy", "step_number"]


class AlertNotificationViewSet(ListModelMixin, RetrieveModelMixin, GenericViewSet):
    """Read-only history of notification deliveries."""

    queryset = AlertNotification.objects.select_related("user", "team", "alert_event").all()
    serializer_class = AlertNotificationSerializer
    filterset_fields = ["alert_event", "status", "channel", "team"]
    ordering = ["-created_at"]


class OnCallScheduleViewSet(viewsets.ModelViewSet):
    """On-call schedules and their shifts."""

    queryset = OnCallSchedule.objects.select_related("team").prefetch_related("shifts").all()
    serializer_class = OnCallScheduleSerializer
    filterset_fields = ["team"]

    @action(detail=True, methods=["get", "post"])
    def shifts(self, request, pk=None):
        schedule = self.get_object()
        if request.method == "GET":
            return Response(OnCallShiftSerializer(schedule.shifts.select_related("user"), many=True).data)
        ser = OnCallShiftSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        ser.save(schedule=schedule)
        return Response(ser.data, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=["get"], url_path="current")
    def current(self, request):
        """Who is on-call right now, per team."""
        out = []
        for team in Team.objects.all():
            user = engine.get_on_call_user(team)
            out.append({
                "team": team.id, "team_name": team.name,
                "user": user.id if user else None,
                "username": user.username if user else None,
            })
        return Response(out)


class OnCallShiftViewSet(viewsets.ModelViewSet):
    queryset = OnCallShift.objects.select_related("user", "schedule").all()
    serializer_class = OnCallShiftSerializer
    filterset_fields = ["schedule", "user"]


class MaintenanceWindowViewSet(viewsets.ModelViewSet):
    """Scheduled maintenance windows that suppress alerts while active."""

    queryset = MaintenanceWindow.objects.prefetch_related("devices", "sites").all()
    serializer_class = MaintenanceWindowSerializer
    filterset_fields = ["is_active", "recurrence"]
    search_fields = ["name"]
    ordering = ["start_time"]

    def perform_create(self, serializer):
        user = self.request.user if self.request.user.is_authenticated else None
        serializer.save(created_by=user)

    @action(detail=False, methods=["get"])
    def active(self, request):
        """Currently-active maintenance windows (for the dashboard widget)."""
        from .maintenance import active_windows
        return Response(self.get_serializer(active_windows(), many=True).data)

    @action(detail=True, methods=["post"], url_path="end-now")
    def end_now(self, request, pk=None):
        """End a window immediately (set end_time = now)."""
        from django.utils import timezone
        w = self.get_object()
        w.end_time = timezone.now()
        w.save(update_fields=["end_time", "updated_at"])
        return Response(self.get_serializer(w).data)
