"""Servers API (`/api/servers/`) — agent-monitored servers.

These are the same underlying ``Agent`` rows as ``/api/agents/`` but framed as
servers: role assignment (manual + auto-detect) here, plus list/detail + metrics
(see the Servers page work). Admin/JWT-authed via the default permission.
"""
from __future__ import annotations

from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.alerts.models import AlertEvent
from apps.core.permissions import CapabilityViewSetMixin

from .detection import auto_detect_roles
from .metrics_read import detail_metrics, metric_history
from .models import Agent, AgentRole, ServerRole
from .serializers import AssignedRoleSerializer, ServerSerializer


class ServerViewSet(CapabilityViewSetMixin, viewsets.ReadOnlyModelViewSet):
    """List/retrieve agent-monitored servers, their metrics, and role assignments."""
    view_capability = "agent:view"
    write_capability = "agent:edit"

    queryset = (Agent.objects.exclude(status=Agent.Status.REVOKED)
                .select_related("device", "device__site")
                .prefetch_related("assigned_roles__role"))
    serializer_class = ServerSerializer

    def retrieve(self, request, *args, **kwargs):
        """Full server detail: list fields + current per-core/mount/iface metrics
        + the 5 most recent alerts for the linked device."""
        server = self.get_object()
        data = self.get_serializer(server).data
        device_id = str(server.device_id or server.id)
        data["detail_metrics"] = detail_metrics(device_id)
        data["recent_alerts"] = self._recent_alerts(server)
        return Response(data)

    @action(detail=True, methods=["get"], url_path="metrics/history")
    def metrics_history(self, request, pk=None):
        """Windowed time-series for charting. ?metric=cpu|memory|disk|load|network
        &range=1h|6h|24h|7d."""
        server = self.get_object()
        device_id = str(server.device_id or server.id)
        metric = request.query_params.get("metric", "cpu")
        rng = request.query_params.get("range", "1h")
        return Response(metric_history(device_id, metric, rng))

    @staticmethod
    def _recent_alerts(server) -> list[dict]:
        if not server.device_id:
            return []
        events = (AlertEvent.objects.filter(labels__device_id=server.device_id)
                  .select_related("rule").order_by("-created_at")[:5])
        return [{
            "id": e.id, "name": e.rule.name, "severity": e.rule.severity,
            "state": e.state, "summary": (e.annotations or {}).get("summary", ""),
            "created_at": e.created_at,
        } for e in events]

    @action(detail=True, methods=["get", "post"])
    def roles(self, request, pk=None):
        """GET: assigned roles + latest check status. POST {role_id}: assign."""
        server = self.get_object()
        if request.method == "POST":
            role = ServerRole.objects.filter(pk=request.data.get("role_id")).first()
            if not role:
                return Response({"detail": "role_id not found."},
                                status=status.HTTP_400_BAD_REQUEST)
            assignment, created = AgentRole.objects.get_or_create(
                agent=server, role=role,
                defaults={"assigned_by": request.user if request.user.is_authenticated else None},
            )
            return Response(AssignedRoleSerializer(assignment).data,
                            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)
        assignments = server.assigned_roles.select_related("role").all()
        return Response(AssignedRoleSerializer(assignments, many=True).data)

    @action(detail=True, methods=["delete"], url_path=r"roles/(?P<role_id>[^/.]+)")
    def remove_role(self, request, pk=None, role_id=None):
        """Unassign a role from the server."""
        server = self.get_object()
        deleted, _ = AgentRole.objects.filter(agent=server, role_id=role_id).delete()
        if not deleted:
            return Response({"detail": "Role not assigned."}, status=status.HTTP_404_NOT_FOUND)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=["post"], url_path="detect-roles")
    def detect_roles(self, request, pk=None):
        """Auto-detect candidate roles from the server's reported running services."""
        server = self.get_object()
        return Response({"detected": auto_detect_roles(server)})
