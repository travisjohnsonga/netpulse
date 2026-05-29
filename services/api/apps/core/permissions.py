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
