"""Servers API (`/api/servers/`) — agent-monitored servers.

These are the same underlying ``Agent`` rows as ``/api/agents/`` but framed as
servers: role assignment (manual + auto-detect) here, plus list/detail + metrics
(see the Servers page work). Admin/JWT-authed via the default permission.
"""
from __future__ import annotations

from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from .detection import auto_detect_roles
from .models import Agent, AgentRole, ServerRole
from .serializers import AgentSerializer, AssignedRoleSerializer


class ServerViewSet(viewsets.ReadOnlyModelViewSet):
    """List/retrieve agent-monitored servers and manage their role assignments."""
    queryset = (Agent.objects.exclude(status=Agent.Status.REVOKED)
                .select_related("device", "device__site")
                .prefetch_related("assigned_roles__role"))
    serializer_class = AgentSerializer

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
