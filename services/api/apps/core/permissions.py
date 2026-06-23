"""
NetPulse RBAC permission classes for Django REST Framework.

Role hierarchy:
  admin    – full access; can also log into Django admin panel
  engineer – read/write on all operational endpoints
  api      – service-account tokens; same CRUD access as engineer, no admin panel
  viewer   – read-only (safe HTTP methods: GET, HEAD, OPTIONS)

Superusers bypass all role checks (django.contrib.admin parity).
"""
from __future__ import annotations

from rest_framework.permissions import SAFE_METHODS, BasePermission

from apps.core.capabilities import ALL_CAPABILITIES
from apps.core.models import Role

_WRITE_ROLES = frozenset({Role.ADMIN, Role.ENGINEER, Role.API})
_READ_ROLES  = frozenset({Role.ADMIN, Role.ENGINEER, Role.API, Role.VIEWER})


def _role(request) -> str | None:
    return getattr(request.user, "role", None)


class NetPulsePermission(BasePermission):
    """
    Standard operational permission used on most endpoints:
      - Unauthenticated → 401
      - Any valid role + safe method → allowed
      - admin / engineer / api + any method → allowed
      - viewer + unsafe method → 403
      - No recognised role → 403
    """

    def has_permission(self, request, view) -> bool:
        if not request.user or not request.user.is_authenticated:
            return False
        if request.user.is_superuser:
            return True
        role = _role(request)
        if request.method in SAFE_METHODS:
            return role in _READ_ROLES
        return role in _WRITE_ROLES


class AdminOnly(BasePermission):
    """
    Restricts access to admin-role users and Django superusers.
    Use for endpoints that manage platform configuration or user accounts.
    """

    def has_permission(self, request, view) -> bool:
        if not request.user or not request.user.is_authenticated:
            return False
        if request.user.is_superuser:
            return True
        return _role(request) == Role.ADMIN


class IsAnyRole(BasePermission):
    """
    Requires authentication and any recognised NetPulse role.
    Useful for read-only endpoints that should be visible to all logged-in users.
    """

    def has_permission(self, request, view) -> bool:
        if not request.user or not request.user.is_authenticated:
            return False
        if request.user.is_superuser:
            return True
        return _role(request) in _READ_ROLES


# ── RBAC Track 2: capability-based authorization (Phase A — defined, not yet
# applied to any viewset). has_capability resolves a user → their RBACRole → its
# capability set; HasCapability is the DRF gate Phase B will attach to viewsets.

def has_capability(user, capability: str) -> bool:
    """True if ``user`` holds ``capability``.

    Superusers always pass (django.contrib.admin parity). Otherwise the user's
    ``rbac_role`` capability set is consulted; a user with no role has none.
    """
    if not user or not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, "is_superuser", False):
        return True
    role = getattr(user, "rbac_role", None)
    if role is None:
        return False
    return capability in role.capability_set()


def HasCapability(required_capability: str):
    """DRF permission factory gating on a single capability.

    Usage: ``permission_classes = [HasCapability("device:edit")]``.
    Returns a ``BasePermission`` subclass bound to ``required_capability``.

    The capability is validated against :data:`ALL_CAPABILITIES` at construction
    so a typo'd cap string fails loudly at import time, not silently at request
    time (a misspelled cap would otherwise deny everyone under deny-by-default).
    """
    if required_capability not in ALL_CAPABILITIES:
        raise ValueError(f"Unknown capability {required_capability!r} — not in ALL_CAPABILITIES")

    class _HasCapability(BasePermission):
        capability = required_capability

        def has_permission(self, request, view) -> bool:
            return has_capability(request.user, required_capability)

    _HasCapability.__name__ = f"HasCapability[{required_capability}]"
    return _HasCapability


class DenyByDefault(BasePermission):
    """Deny-by-default — the project DEFAULT_PERMISSION_CLASSES (Phase B).

    Any view that declares no permission_classes/get_permissions inherits this and
    is DENIED (fails closed), so a forgotten capability can never silently grant
    viewer-read/engineer-write. The ONE exception is superuser (django-admin
    parity). Every reachable endpoint must explicitly declare HasCapability /
    CapabilityViewSetMixin (or be on the unauthenticated allowlist).
    """

    message = "No capability declared for this endpoint."

    def has_permission(self, request, view) -> bool:
        return bool(getattr(request.user, "is_superuser", False))


class CapabilityViewSetMixin:
    """Mixin that gates a ViewSet on capabilities by method class.

    Set ``view_capability`` (safe methods) and ``write_capability`` (unsafe
    methods; defaults to ``view_capability``). For read-only viewsets set just
    ``view_capability``. Actions needing a different cap override get_permissions.
    Uses HasCapability under the hood, so superuser bypass + role resolution are
    consistent. A viewset that sets neither attribute denies (fails closed).
    """

    view_capability: str | None = None
    write_capability: str | None = None

    def get_permissions(self):
        safe = self.request.method in SAFE_METHODS
        cap = self.view_capability if safe else (self.write_capability or self.view_capability)
        if cap is None:
            return [DenyByDefault()]
        return [HasCapability(cap)()]
