import datetime
import os
import socket
import time
import urllib.request
import urllib.error

from django.contrib.auth import get_user_model
from django.db import connection
from django.db.models import Q
from django.db.utils import OperationalError
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import generics, serializers, status, viewsets
from rest_framework.decorators import api_view, permission_classes
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Role, SystemSetting, UserPreferences
from .permissions import AdminOnly
from .serializers import (
    AdminUserSerializer,
    ChangePasswordSerializer,
    MeSerializer,
    UserPreferencesSerializer,
)

User = get_user_model()


def _ssl_cert_days_remaining():
    """Whole days until the web-UI TLS cert expires, or None if unavailable.

    Reads the cert at settings.SSL_CERT_PATH. Returns None when the cert is
    missing or unreadable (e.g. before one has been generated) so the dashboard
    simply omits the expiry warning rather than erroring. Negative values mean
    the cert has already expired.
    """
    from django.conf import settings as dj_settings

    cert_path = getattr(dj_settings, "SSL_CERT_PATH", "") or ""
    if not cert_path or not os.path.isfile(cert_path):
        return None
    try:
        from cryptography import x509

        with open(cert_path, "rb") as fh:
            cert = x509.load_pem_x509_certificate(fh.read())
        # cryptography ≥42 deprecates naive not_valid_after in favour of the
        # tz-aware *_utc; support both so we work across pinned versions.
        try:
            expiry = cert.not_valid_after_utc
            now = datetime.datetime.now(datetime.timezone.utc)
        except AttributeError:  # pragma: no cover - older cryptography
            expiry = cert.not_valid_after
            now = datetime.datetime.utcnow()
        return (expiry - now).days
    except Exception:  # noqa: BLE001 — health must never raise on a bad cert
        return None


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
            "setup_complete": bool(getattr(dj_settings, "SETUP_COMPLETE", False)),
            "openbao": "healthy" if _openbao_healthy() else "unavailable",
            "collector_ip": getattr(dj_settings, "COLLECTOR_IP", "") or "",
            "ssl_cert_days_remaining": _ssl_cert_days_remaining(),
        },
        status=200 if db_ok else 503,
    )


def _openbao_healthy() -> bool:
    """True when OpenBao answers /v1/sys/health as initialized + unsealed (200)."""
    from django.conf import settings as dj_settings

    addr = getattr(dj_settings, "OPENBAO_ADDR", "") or os.environ.get("OPENBAO_ADDR", "http://openbao:8200")
    try:
        with urllib.request.urlopen(f"{addr.rstrip('/')}/v1/sys/health", timeout=2.0) as resp:
            return resp.status == 200
    except urllib.error.HTTPError as exc:
        # 429 = unsealed standby (still usable); anything else (501 uninit,
        # 503 sealed) is not healthy.
        return exc.code == 429
    except Exception:
        return False


def _netpulse_version() -> str:
    """Best-effort version string (git describe), else env, else 'unknown'."""
    env_ver = os.environ.get("NETPULSE_VERSION", "")
    if env_ver:
        return env_ver
    try:
        import subprocess

        out = subprocess.run(
            ["git", "describe", "--tags", "--always"],
            capture_output=True, text=True, timeout=2.0,
        )
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except Exception:
        pass
    return "unknown"


@extend_schema(
    summary="First-run setup status (no auth)",
    description="Whether initial setup is complete and core dependencies are healthy. "
                "Used by the frontend before login to gate the app on the /setup page.",
    responses=inline_serializer(
        "SetupStatus",
        {
            "setup_complete": serializers.BooleanField(),
            "openbao_healthy": serializers.BooleanField(),
            "database_healthy": serializers.BooleanField(),
            "version": serializers.CharField(),
        },
    ),
)
@api_view(["GET"])
@permission_classes([AllowAny])
def setup_status(request):
    from django.conf import settings as dj_settings

    try:
        connection.ensure_connection()
        db_ok = True
    except OperationalError:
        db_ok = False

    return Response({
        "setup_complete": bool(getattr(dj_settings, "SETUP_COMPLETE", False)),
        "openbao_healthy": _openbao_healthy(),
        "database_healthy": db_ok,
        "version": _netpulse_version(),
    })


def _tcp_ok(host: str, port: int, timeout: float = 2.0) -> bool:
    return _tcp_probe(host, port, timeout)[0]


def _http_ok(url: str, timeout: float = 2.0) -> bool:
    return _http_probe(url, timeout)[0]


def _tcp_probe(host: str, port: int, timeout: float = 2.0):
    """(reachable, response_ms). response_ms is None when unreachable."""
    start = time.monotonic()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True, round((time.monotonic() - start) * 1000, 1)
    except OSError:
        return False, None


def _http_probe(url: str, timeout: float = 2.0):
    """(ok, response_ms). ok = the endpoint answered below 500."""
    start = time.monotonic()
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.status < 500, round((time.monotonic() - start) * 1000, 1)
    except urllib.error.HTTPError as exc:
        return exc.code < 500, round((time.monotonic() - start) * 1000, 1)
    except Exception:
        return False, None


def _openbao_probe(timeout: float = 2.0):
    """(healthy, response_ms) for OpenBao /v1/sys/health (see _openbao_healthy)."""
    from django.conf import settings as dj_settings

    addr = getattr(dj_settings, "OPENBAO_ADDR", "") or os.environ.get(
        "OPENBAO_ADDR", "http://openbao:8200"
    )
    start = time.monotonic()
    try:
        with urllib.request.urlopen(f"{addr.rstrip('/')}/v1/sys/health", timeout=timeout) as resp:
            return resp.status == 200, round((time.monotonic() - start) * 1000, 1)
    except urllib.error.HTTPError as exc:
        # 429 = unsealed standby (still usable).
        return exc.code == 429, round((time.monotonic() - start) * 1000, 1)
    except Exception:
        return False, None


@extend_schema(
    summary="Infrastructure service health",
    description="Per-service status + response time for postgres, valkey, nats, "
                "influxdb, opensearch and openbao, plus the platform version.",
    responses=inline_serializer(
        "InfrastructureHealth",
        {
            "checked_at": serializers.CharField(),
            "version": serializers.CharField(),
            "services": serializers.DictField(
                child=inline_serializer(
                    "InfraServiceStatus",
                    {
                        "ok": serializers.BooleanField(),
                        "response_ms": serializers.FloatField(allow_null=True),
                    },
                ),
            ),
        },
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

    start = time.monotonic()
    try:
        connection.ensure_connection()
        postgres = {"ok": True, "response_ms": round((time.monotonic() - start) * 1000, 1)}
    except OperationalError:
        postgres = {"ok": False, "response_ms": None}

    def _svc(probe):
        ok, ms = probe
        return {"ok": ok, "response_ms": ms}

    services = {
        "postgres": postgres,
        "valkey": _svc(_tcp_probe(valkey_host, valkey_port)),
        "nats": _svc(_tcp_probe(nats_host, 4222)),
        "influxdb": _svc(_http_probe(f"{influxdb_url}/health")),
        "opensearch": _svc(
            _http_probe(f"http://{opensearch_host}:{opensearch_port}/_cluster/health")
        ),
        "openbao": _svc(_openbao_probe()),
    }

    return Response(
        {
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "version": _netpulse_version(),
            "services": services,
        }
    )


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


class OnboardingStatusView(APIView):
    """
    Whether to show the Get Started wizard for the current user.

    Shown only when the system is genuinely empty AND this user hasn't dismissed
    it: ``not Device.objects.exists() and not prefs.onboarding_completed``. Once
    any device exists, the wizard is hidden for everyone.
    """

    @extend_schema(
        summary="Onboarding status for the current user",
        responses=inline_serializer(
            "OnboardingStatus",
            {
                "show_onboarding": serializers.BooleanField(),
                "reasons": inline_serializer(
                    "OnboardingReasons",
                    {
                        "has_devices": serializers.BooleanField(),
                        "user_completed": serializers.BooleanField(),
                    },
                ),
            },
        ),
    )
    def get(self, request):
        from apps.devices.models import Device

        has_devices = Device.objects.exists()
        prefs = UserPreferences.for_user(request.user)
        user_completed = prefs.onboarding_completed
        return Response(
            {
                "show_onboarding": not has_devices and not user_completed,
                "reasons": {"has_devices": has_devices, "user_completed": user_completed},
            }
        )


class OnboardingCompleteView(APIView):
    """Mark the current user's onboarding as complete (dismiss the wizard)."""

    @extend_schema(
        summary="Mark onboarding complete for the current user",
        request=None,
        responses=inline_serializer(
            "OnboardingComplete", {"onboarding_completed": serializers.BooleanField()}
        ),
    )
    def post(self, request):
        prefs = UserPreferences.for_user(request.user)
        if not prefs.onboarding_completed:
            prefs.onboarding_completed = True
            prefs.save(update_fields=["onboarding_completed", "updated_at"])
        return Response({"onboarding_completed": True})


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


class HostnameDisplayView(APIView):
    """Get or update the device-hostname display mode (strip domain suffix).

    Display-only: changing this never affects the stored hostname used for
    SSH/SNMP/syslog. PUT requires admin.
    """

    def get_permissions(self):
        if self.request.method == "GET":
            return super().get_permissions()
        return [AdminOnly()]

    def _state(self):
        from .hostname import hostname_display_config

        strip_enabled, suffix = hostname_display_config()
        return {"mode": "strip" if strip_enabled else "full", "domain_suffix": suffix}

    @extend_schema(
        summary="Hostname display settings",
        responses=inline_serializer(
            "HostnameDisplay",
            {
                "mode": serializers.ChoiceField(choices=["strip", "full"]),
                "domain_suffix": serializers.CharField(allow_blank=True),
            },
        ),
    )
    def get(self, request):
        return Response(self._state())

    @extend_schema(
        request=inline_serializer(
            "HostnameDisplayUpdate",
            {
                "mode": serializers.ChoiceField(choices=["strip", "full"]),
                "domain_suffix": serializers.CharField(allow_blank=True, required=False),
            },
        ),
        responses=inline_serializer(
            "HostnameDisplay",
            {
                "mode": serializers.ChoiceField(choices=["strip", "full"]),
                "domain_suffix": serializers.CharField(allow_blank=True),
            },
        ),
    )
    def put(self, request):
        mode = request.data.get("mode")
        if mode not in ("strip", "full"):
            raise ValidationError({"mode": "Must be 'strip' or 'full'."})
        suffix = request.data.get("domain_suffix", "")
        if suffix is None:
            suffix = ""
        SystemSetting.set("hostname_display_mode", mode)
        SystemSetting.set("domain_suffix", str(suffix).strip())
        return Response(self._state())


def _active_admin_q():
    """Users who count as active administrators (admin role or superuser)."""
    return Q(is_active=True) & (Q(role=Role.ADMIN) | Q(is_superuser=True))


class UserViewSet(viewsets.ModelViewSet):
    """
    Admin-only user management (Settings → Users).

    Full CRUD over user accounts. Two safety guards prevent locking yourself or
    the whole org out of administration:
      - you cannot delete your own account, and
      - you cannot delete, demote or deactivate the last active administrator.
    """

    queryset = User.objects.all().order_by("username")
    serializer_class = AdminUserSerializer
    permission_classes = [AdminOnly]
    filterset_fields = ["role", "is_active"]
    search_fields = ["username", "email", "first_name", "last_name"]

    @staticmethod
    def _is_last_admin(user) -> bool:
        """True if `user` is an active admin and the only one left."""
        if not (user.is_active and (user.role == Role.ADMIN or user.is_superuser)):
            return False
        return not User.objects.filter(_active_admin_q()).exclude(pk=user.pk).exists()

    def destroy(self, request, *args, **kwargs):
        user = self.get_object()
        if user.pk == request.user.pk:
            return Response(
                {"error": "You cannot delete your own account."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if self._is_last_admin(user):
            return Response(
                {"error": "Cannot delete the last administrator."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return super().destroy(request, *args, **kwargs)

    def perform_update(self, serializer):
        """Block changes that would remove the last administrator."""
        user = serializer.instance
        if self._is_last_admin(user):
            new_role = serializer.validated_data.get("role", user.role)
            new_active = serializer.validated_data.get("is_active", user.is_active)
            demoted = new_role != Role.ADMIN and not user.is_superuser
            if not new_active or demoted:
                raise ValidationError(
                    {"error": "Cannot demote or deactivate the last administrator."}
                )
        serializer.save()


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
