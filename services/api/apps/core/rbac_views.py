"""RBAC Track 2 Phase C — role-management API.

Makes roles editable by admins via API (previously only via migration). Three
surfaces, all gated by the ``rbac:manage`` capability:

- ``GET /api/rbac/capabilities/`` — the code-defined capability catalog, grouped
  by prefix for the UI. Read-only (capabilities are fixed in code).
- ``/api/rbac/roles/`` — CRUD over roles. SYSTEM roles (superadmin/admin/engineer/
  api/viewer) are READ-ONLY here (canonical); customization is via NEW custom
  roles only.
- user-role assignment lives on the user API (UserViewSet.assign_rbac_role).

THE GUARDRAIL: a user with ``rbac:manage`` may never grant a capability they do
not themselves hold (a role's assigned cap set must be ⊆ the requester's own
caps). Without it, rbac:manage silently equals superadmin. Superusers bypass.
"""
from __future__ import annotations

from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers, status, viewsets
from rest_framework.exceptions import PermissionDenied
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from .capabilities import ALL_CAPABILITIES
from .models import RBACRole
from .permissions import HasCapability, capabilities_of

_SYSTEM_READONLY_MSG = "System roles are read-only; create a custom role instead."


class CapabilityCatalogView(APIView):
    """Read-only capability catalog, grouped by prefix (e.g. device, integration)."""

    permission_classes = [HasCapability("rbac:manage")]

    def get(self, request):
        groups: dict[str, list[dict]] = {}
        for cap in sorted(ALL_CAPABILITIES):
            group = cap.split(":", 1)[0]
            groups.setdefault(group, []).append({"name": cap})
        return Response([
            {"group": group, "capabilities": caps}
            for group, caps in sorted(groups.items())
        ])


class RoleSerializer(serializers.ModelSerializer):
    user_count = serializers.SerializerMethodField()

    class Meta:
        model = RBACRole
        fields = (
            "id", "name", "description", "capabilities",
            "is_system", "is_immutable", "user_count", "created_at", "updated_at",
        )
        # is_system/is_immutable are not client-settable — a role created here is
        # always a custom (non-system) role.
        read_only_fields = (
            "id", "is_system", "is_immutable", "user_count", "created_at", "updated_at",
        )

    def get_user_count(self, obj) -> int:
        return obj.users.count()

    def validate_capabilities(self, value):
        if not isinstance(value, list):
            raise serializers.ValidationError("Must be a list of capability strings.")
        unknown = set(value) - ALL_CAPABILITIES
        if unknown:
            raise serializers.ValidationError(f"Unknown capabilities: {sorted(unknown)}")
        return value


class RoleViewSet(viewsets.ModelViewSet):
    """CRUD over RBAC roles (rbac:manage). System roles are read-only; custom roles
    are full CRUD, subject to the anti-escalation guardrail."""

    queryset = RBACRole.objects.all().order_by("name")
    serializer_class = RoleSerializer
    permission_classes = [HasCapability("rbac:manage")]

    # ── guardrail ──────────────────────────────────────────────────────────────
    def _check_escalation(self, capabilities):
        """A role's assigned capabilities must be ⊆ the requester's own caps."""
        disallowed = set(capabilities or []) - capabilities_of(self.request.user)
        if disallowed:
            raise PermissionDenied(
                "You cannot grant capabilities you do not hold: "
                f"{sorted(disallowed)}")

    @staticmethod
    def _block_system(instance):
        if instance.is_system:
            raise PermissionDenied(_SYSTEM_READONLY_MSG)

    # ── create (custom roles only) ───────────────────────────────────────────
    def perform_create(self, serializer):
        self._check_escalation(serializer.validated_data.get("capabilities", []))
        try:
            serializer.save(is_system=False, is_immutable=False)
        except DjangoValidationError as exc:
            raise DRFValidationError(getattr(exc, "message_dict", exc.messages))

    # ── update (custom roles only; system roles 403) ─────────────────────────
    def update(self, request, *args, **kwargs):
        self._block_system(self.get_object())
        return super().update(request, *args, **kwargs)

    def perform_update(self, serializer):
        # Only check escalation on caps actually being written (a description-only
        # PATCH that doesn't touch capabilities doesn't "grant" anything).
        if "capabilities" in serializer.validated_data:
            self._check_escalation(serializer.validated_data["capabilities"])
        try:
            serializer.save()
        except DjangoValidationError as exc:
            raise DRFValidationError(getattr(exc, "message_dict", exc.messages))

    # ── delete (custom + unused only) ────────────────────────────────────────
    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        self._block_system(instance)
        in_use = instance.users.count()
        if in_use:
            return Response(
                {"detail": f"Role is assigned to {in_use} user(s); reassign them "
                           "before deleting."},
                status=status.HTTP_409_CONFLICT)
        return super().destroy(request, *args, **kwargs)
