import os
import socket
import urllib.request
import urllib.error

from django.db import connection
from django.db.utils import OperationalError
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import generics, serializers
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import UserPreferences
from .serializers import (
    ChangePasswordSerializer,
    MeSerializer,
    UserPreferencesSerializer,
)


@extend_schema(
    summary="Liveness / database health",
    description="Returns overall service status, DB reachability, the configured "
                "collector IP, and SSL certificate days-remaining (if any).",
    responses=inline_serializer(
        "HealthStatus",
        {
            "status": serializers.CharField(),
            "db": serializers.BooleanField(),
            "collector_ip": serializers.CharField(allow_blank=True),
            "ssl_cert_days_remaining": serializers.IntegerField(allow_null=True),
        },
    ),
)
@api_view(["GET"])
@permission_classes([AllowAny])
def health(request):
    from django.conf import settings as dj_settings

    try:
        connection.ensure_connection()
        db_ok = True
    except OperationalError:
        db_ok = False

    status = "ok" if db_ok else "degraded"
    return Response(
        {
            "status": status,
            "db": db_ok,
            "collector_ip": getattr(dj_settings, "COLLECTOR_IP", "") or "",
            "ssl_cert_days_remaining": None,
        },
        status=200 if db_ok else 503,
    )


def _tcp_ok(host: str, port: int, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _http_ok(url: str, timeout: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.status < 500
    except Exception:
        return False


@extend_schema(
    summary="Infrastructure service health",
    description="Per-service reachability for postgres, valkey, nats, influxdb and opensearch.",
    responses=inline_serializer(
        "InfrastructureHealth",
        {"services": serializers.DictField(child=serializers.BooleanField())},
    ),
)
@api_view(["GET"])
@permission_classes([AllowAny])
def infrastructure_health(request):
    valkey_host = os.environ.get("VALKEY_HOST", "valkey")
    valkey_port = int(os.environ.get("VALKEY_PORT", "6379"))
    nats_host = os.environ.get("NATS_HOST", "nats")
    influxdb_url = os.environ.get("INFLUXDB_URL", "http://influxdb:8086")
    opensearch_host = os.environ.get("OPENSEARCH_HOST", "opensearch")
    opensearch_port = int(os.environ.get("OPENSEARCH_PORT", "9200"))

    try:
        connection.ensure_connection()
        postgres_ok = True
    except OperationalError:
        postgres_ok = False

    services = {
        "postgres": postgres_ok,
        "valkey": _tcp_ok(valkey_host, valkey_port),
        "nats": _tcp_ok(nats_host, 4222),
        "influxdb": _http_ok(f"{influxdb_url}/health"),
        "opensearch": _http_ok(
            f"http://{opensearch_host}:{opensearch_port}/_cluster/health"
        ),
    }

    return Response({"services": services})


# ── user profile & preferences ───────────────────────────────────────────────


class MeView(generics.RetrieveUpdateAPIView):
    """Get or update the current user's account info (with nested preferences)."""

    serializer_class = MeSerializer

    def get_object(self):
        # Ensure a preferences row exists so it's always present in the response.
        UserPreferences.for_user(self.request.user)
        return self.request.user


class MyPreferencesView(generics.RetrieveUpdateAPIView):
    """Get or update the current user's preferences (auto-created on first access)."""

    serializer_class = UserPreferencesSerializer

    def get_object(self):
        return UserPreferences.for_user(self.request.user)


class SystemSettingsView(APIView):
    """
    Platform-level system settings the frontend needs at runtime.

    Currently exposes the config-push master switch so the UI can disable the
    "Push to Device" controls without hardcoding the flag, plus the configured
    collector IP for convenience.
    """

    @extend_schema(
        summary="System settings (config-push flag, collector IP)",
        responses=inline_serializer(
            "SystemSettings",
            {
                "allow_config_push": serializers.BooleanField(),
                "collector_ip": serializers.CharField(allow_blank=True),
            },
        ),
    )
    def get(self, request):
        from django.conf import settings as dj_settings

        return Response(
            {
                "allow_config_push": bool(getattr(dj_settings, "ALLOW_CONFIG_PUSH", False)),
                "collector_ip": getattr(dj_settings, "COLLECTOR_IP", "") or "",
            }
        )


class ChangePasswordView(APIView):
    """Change the current user's password (requires the current password)."""

    @extend_schema(
        request=ChangePasswordSerializer,
        responses=inline_serializer("ChangePasswordResponse", {"detail": serializers.CharField()}),
        summary="Change current user's password",
    )
    def post(self, request):
        ser = ChangePasswordSerializer(data=request.data, context={"request": request})
        ser.is_valid(raise_exception=True)
        ser.save()
        return Response({"detail": "Password updated."})
