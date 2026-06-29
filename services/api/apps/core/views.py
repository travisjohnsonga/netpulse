import datetime
import os
import socket
import time
import urllib.request
import urllib.error

from apps.core.net_safety import validate_outbound_url

from django.contrib.auth import get_user_model
from django.db import connection
from django.db.models import Q
from django.db.utils import OperationalError
from django_filters import rest_framework as _df
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import generics, serializers, status, viewsets
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import AllowAny, IsAuthenticated, SAFE_METHODS
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Role, SystemSetting, UserPreferences
from .permissions import HasCapability
from .serializers import (
    AdminUserSerializer,
    AuditLogSerializer,
    ChangePasswordSerializer,
    MeSerializer,
    NetPulseTokenObtainPairSerializer,
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
            # Authoritative server wall-clock (UTC, ISO 8601) — anchors the UI
            # footer clock so it shows true server time even if the browser clock
            # is wrong. TIME_ZONE=UTC, so this is the server's UTC now.
            "server_time": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        },
        status=200 if db_ok else 503,
    )


def _openbao_healthy() -> bool:
    """True when OpenBao answers /v1/sys/health as initialized + unsealed (200)."""
    from django.conf import settings as dj_settings

    addr = getattr(dj_settings, "OPENBAO_ADDR", "") or os.environ.get("OPENBAO_ADDR", "http://openbao:8200")
    try:
        url = validate_outbound_url(f"{addr.rstrip('/')}/v1/sys/health", block_metadata=False)
        with urllib.request.urlopen(url, timeout=2.0) as resp:  # nosec B310 — scheme allowlisted (http/https) by validate_outbound_url() on the line above
            return resp.status == 200
    except urllib.error.HTTPError as exc:
        # 429 = unsealed standby (still usable); anything else (501 uninit,
        # 503 sealed) is not healthy.
        return exc.code == 429
    except Exception:
        return False


def _netpulse_version() -> str:
    """Canonical version — TWO legible tiers, no hidden file:

    1. explicit env override ``SPANE_VERSION``/``NETPULSE_VERSION`` (set in deploy
       config / CI / the update script — VISIBLE; ``dev``/empty ignored);
    2. ``settings.VERSION`` — the git **app-tag**-derived version (the real
       running code).

    The old bind-mounted ``/app/VERSION`` file tier was removed: a file that
    silently overrides the reported version is action-at-a-distance — for a
    monitoring/security tool the version must reflect the running code, with the
    only override being explicit in the deploy env. Every reader (infra-check,
    /api/version/, the badge, logs) resolves through this one path."""
    for var in ("SPANE_VERSION", "NETPULSE_VERSION"):
        v = os.environ.get(var, "").strip()
        if v and v.lower() != "dev":
            return v
    from django.conf import settings
    return getattr(settings, "VERSION", "") or "unknown"


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

    # NOTE: no "version" here — this endpoint is unauthenticated, so the exact
    # build version is intentionally withheld (info-leak / fingerprinting). The
    # authenticated /api/version/ and infrastructure-health endpoints still report it.
    return Response({
        "setup_complete": bool(getattr(dj_settings, "SETUP_COMPLETE", False)),
        "openbao_healthy": _openbao_healthy(),
        "database_healthy": db_ok,
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
        url = validate_outbound_url(url, block_metadata=False)
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # nosec B310 — scheme allowlisted (http/https) by validate_outbound_url() on the line above
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
        url = validate_outbound_url(f"{addr.rstrip('/')}/v1/sys/health", block_metadata=False)
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # nosec B310 — scheme allowlisted (http/https) by validate_outbound_url() on the line above
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

    # Notification-delivery health — so external monitoring catches a silent
    # dispatch failure ("watch the watcher"). Best-effort; never breaks /health.
    try:
        from apps.alerts.delivery_health import delivery_health
        dh = delivery_health(window_minutes=60)
        notification_delivery = {
            "ok": dh["healthy"], "channels_failing": dh["channels_failing"],
            "recent_failures": dh["recent_failures"],
        }
    except Exception:  # noqa: BLE001
        notification_delivery = {"ok": True, "channels_failing": 0, "recent_failures": 0}

    return Response(
        {
            "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "version": _netpulse_version(),
            "services": services,
            "notification_delivery": notification_delivery,
        }
    )


# ── user profile & preferences ───────────────────────────────────────────────


class MeView(generics.RetrieveUpdateAPIView):
    """Get or update the current user's account info (with nested preferences)."""

    permission_classes = [IsAuthenticated]
    serializer_class = MeSerializer

    def get_object(self):
        # Ensure a preferences row exists so it's always present in the response.
        UserPreferences.for_user(self.request.user)
        return self.request.user


class MyPreferencesView(generics.RetrieveUpdateAPIView):
    """Get or update the current user's preferences (auto-created on first access)."""

    permission_classes = [IsAuthenticated]
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

    permission_classes = [IsAuthenticated]

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

    permission_classes = [IsAuthenticated]

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

    permission_classes = [IsAuthenticated]

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
        if self.request.method in SAFE_METHODS:
            return [IsAuthenticated()]
        return [HasCapability("system:manage")()]

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
        from .audit import log_event
        from .models import AuditLog
        log_event(AuditLog.EventType.SETTINGS_CHANGED, request=request,
                  description="Settings updated: Hostname display",
                  metadata={"changed_fields": ["hostname_display_mode", "domain_suffix"]})
        return Response(self._state())


class LldpSettingsView(APIView):
    """Get or update the default capability exclusions for the LLDP
    "Not in Inventory" list.

    These capabilities are hidden from the undiscovered-neighbors list by
    default (admins rarely add IP phones / PCs / cable modems to inventory).
    Persisted in SystemSetting so it overrides the env default at runtime. PUT
    requires admin.
    """

    def get_permissions(self):
        if self.request.method in SAFE_METHODS:
            return [IsAuthenticated()]
        return [HasCapability("system:manage")()]

    def _state(self):
        from apps.devices.lldp import (
            DEFAULT_UNMANAGED_CAPABILITIES, KNOWN_CAPABILITIES,
            default_excluded_capabilities,
        )

        return {
            "exclude_capabilities": default_excluded_capabilities(),
            "available_capabilities": list(KNOWN_CAPABILITIES),
            "default_exclude_capabilities": list(DEFAULT_UNMANAGED_CAPABILITIES),
        }

    @extend_schema(
        summary="LLDP undiscovered-neighbor default capability exclusions",
        responses=inline_serializer(
            "LldpSettings",
            {
                "exclude_capabilities": serializers.ListField(child=serializers.CharField()),
                "available_capabilities": serializers.ListField(child=serializers.CharField()),
                "default_exclude_capabilities": serializers.ListField(child=serializers.CharField()),
            },
        ),
    )
    def get(self, request):
        return Response(self._state())

    @extend_schema(
        request=inline_serializer(
            "LldpSettingsUpdate",
            {"exclude_capabilities": serializers.ListField(child=serializers.CharField())},
        ),
        responses=inline_serializer(
            "LldpSettings",
            {
                "exclude_capabilities": serializers.ListField(child=serializers.CharField()),
                "available_capabilities": serializers.ListField(child=serializers.CharField()),
                "default_exclude_capabilities": serializers.ListField(child=serializers.CharField()),
            },
        ),
    )
    def put(self, request):
        from apps.devices.lldp import normalize_capabilities

        caps = request.data.get("exclude_capabilities", [])
        if not isinstance(caps, list):
            raise ValidationError({"exclude_capabilities": "Must be a list of capability tokens."})
        # Canonicalise + de-dupe so stored tokens match what the filter compares.
        tokens = normalize_capabilities([str(c) for c in caps])
        SystemSetting.set("lldp_exclude_capabilities", ",".join(tokens))
        from .audit import log_event
        from .models import AuditLog
        log_event(AuditLog.EventType.SETTINGS_CHANGED, request=request,
                  description="Settings updated: LLDP capability filter",
                  metadata={"changed_fields": ["lldp_exclude_capabilities"]})
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
    filterset_fields = ["role", "is_active"]
    search_fields = ["username", "email", "first_name", "last_name"]

    def get_permissions(self):
        # Direct RBAC-role assignment is gated by rbac:manage (a role-management
        # action); all other user CRUD stays on user:manage.
        if getattr(self, "action", None) == "assign_rbac_role":
            return [HasCapability("rbac:manage")()]
        return [HasCapability("user:manage")()]

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

    def perform_create(self, serializer):
        from .audit import log_event
        from .models import AuditLog
        user = serializer.save()
        log_event(AuditLog.EventType.USER_CREATED, request=self.request, target=user,
                  description=f"User {user.username} created",
                  metadata={"role": user.role})

    def perform_update(self, serializer):
        """Block changes that would remove the last administrator."""
        from .audit import log_event
        from .models import AuditLog
        user = serializer.instance
        old_role = user.role
        if self._is_last_admin(user):
            new_role = serializer.validated_data.get("role", user.role)
            new_active = serializer.validated_data.get("is_active", user.is_active)
            demoted = new_role != Role.ADMIN and not user.is_superuser
            if not new_active or demoted:
                raise ValidationError(
                    {"error": "Cannot demote or deactivate the last administrator."}
                )
        user = serializer.save()
        role_changed = user.role != old_role
        log_event(
            AuditLog.EventType.USER_ROLE_CHANGED if role_changed else AuditLog.EventType.USER_UPDATED,
            request=self.request, target=user,
            description=(f"User {user.username} role changed {old_role} → {user.role}"
                         if role_changed else f"User {user.username} updated"),
        )

    def perform_destroy(self, instance):
        from .audit import log_event
        from .models import AuditLog
        name = instance.username
        log_event(AuditLog.EventType.USER_DELETED, request=self.request, target=instance,
                  description=f"User {name} deleted")
        instance.delete()

    @action(detail=True, methods=["post"], url_path="reset-mfa")
    def reset_mfa(self, request, pk=None):
        """Reset (clear) a user's MFA — lost-device recovery (user:manage).

        Clears the device so the user can re-enroll; if they are a privileged/
        required account this re-triggers forced enrollment on their next login
        (it is NOT a free pass). Never exposes the user's TOTP secret. Audited.
        """
        from .audit import log_event
        from .models import AuditLog
        user = self.get_object()
        device = getattr(user, "mfa_device", None)
        had_mfa = bool(device and device.mfa_enabled)
        if device is not None:
            device.clear()
            device.save()
        log_event(AuditLog.EventType.MFA_RESET_BY_ADMIN, request=request, user=request.user,
                  target=user, description=f"MFA reset for {user.username}",
                  metadata={"had_mfa": had_mfa})
        return Response({"detail": "MFA reset.", "username": user.username, "had_mfa": had_mfa})

    @action(detail=True, methods=["patch"], url_path="rbac-role")
    def assign_rbac_role(self, request, pk=None):
        """Assign a user's RBAC role (rbac:manage). Anti-escalation applies: you
        can't assign a role containing capabilities you don't hold.

        Sets ``rbac_role`` directly (authoritative — bypasses the save()-time
        role→rbac_role sync via the _rbac_role_explicit flag). For a SYSTEM role
        the legacy ``role`` field is aligned too so the JWT role claim / Django
        group / is_admin stay consistent; custom roles have no legacy equivalent,
        so the legacy ``role`` is left as-is and rbac_role drives capabilities.
        """
        from .capabilities import LEGACY_ROLE_TO_SYSTEM
        from .models import AuditLog, RBACRole
        from .permissions import capabilities_of

        user = self.get_object()
        role_id = request.data.get("rbac_role_id", request.data.get("role_id"))
        role = RBACRole.objects.filter(pk=role_id).first()
        if role is None:
            return Response({"detail": "rbac_role_id not found."},
                            status=status.HTTP_400_BAD_REQUEST)

        disallowed = role.capability_set() - capabilities_of(request.user)
        if disallowed:
            raise PermissionDenied(
                "You cannot assign a role with capabilities you do not hold: "
                f"{sorted(disallowed)}")

        user.rbac_role = role
        if role.is_system:
            # superadmin has no legacy equivalent → keep the user's current legacy
            # role for the JWT claim; rbac_role still wins for capabilities.
            reverse = {v: k for k, v in LEGACY_ROLE_TO_SYSTEM.items()}
            user.role = reverse.get(role.name, user.role)
        user._rbac_role_explicit = True
        user.save()

        from .audit import log_event
        log_event(AuditLog.EventType.USER_ROLE_CHANGED, request=request, target=user,
                  description=f"User {user.username} RBAC role set to {role.name}")
        return Response({
            "id": user.id, "username": user.username, "role": user.role,
            "rbac_role": {"name": role.name, "is_system": role.is_system},
            "capabilities": sorted(role.capability_set()),
        })


class ChangePasswordView(APIView):
    """Change the current user's password (requires the current password)."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        request=ChangePasswordSerializer,
        responses=inline_serializer("ChangePasswordResponse", {
            "detail": serializers.CharField(),
            "access": serializers.CharField(),
            "refresh": serializers.CharField(),
        }),
        summary="Change current user's password",
    )
    def post(self, request):
        from .audit import log_event
        from .models import AuditLog
        ser = ChangePasswordSerializer(data=request.data, context={"request": request})
        ser.is_valid(raise_exception=True)
        user = ser.save()
        log_event(AuditLog.EventType.PASSWORD_CHANGED, request=request, user=user,
                  description="Password changed")
        # Mint fresh tokens so the client's claims (notably must_change_password)
        # reflect the change immediately without re-logging-in.
        refresh = NetPulseTokenObtainPairSerializer.get_token(user)
        return Response({
            "detail": "Password updated.",
            "access": str(refresh.access_token),
            "refresh": str(refresh),
        })


# ── Audit log ────────────────────────────────────────────────────────────────

class AuditLogFilter(_df.FilterSet):
    user_id = _df.NumberFilter(field_name="user_id")
    start = _df.IsoDateTimeFilter(field_name="created_at", lookup_expr="gte")
    end = _df.IsoDateTimeFilter(field_name="created_at", lookup_expr="lte")

    class Meta:
        from .models import AuditLog
        model = AuditLog
        fields = ["event_type", "user_id", "target_type", "target_id", "success"]


class AuditLogViewSet(viewsets.ReadOnlyModelViewSet):
    """Read-only access to the audit trail (admin only). Supports filtering by
    event_type / user_id / target / success / date range, free-text search,
    CSV export, and a stats summary."""

    permission_classes = [HasCapability("rbac:manage")]
    filterset_class = AuditLogFilter
    search_fields = ["username", "description", "target_name"]
    ordering_fields = ["created_at", "event_type"]
    ordering = ["-created_at"]

    def get_queryset(self):
        from .models import AuditLog
        return AuditLog.objects.select_related("user").all()

    def get_serializer_class(self):
        return AuditLogSerializer

    @extend_schema(responses=None, summary="Export audit log as CSV")
    @action(detail=False, methods=["get"])
    def export(self, request):
        import csv

        from django.http import HttpResponse

        qs = self.filter_queryset(self.get_queryset())[:10000]
        resp = HttpResponse(content_type="text/csv")
        resp["Content-Disposition"] = 'attachment; filename="audit-log.csv"'
        w = csv.writer(resp)
        w.writerow(["Time", "Event", "User", "IP", "Target", "Target Name",
                    "Success", "Description", "Error"])
        # csv_safe neutralizes formula injection — username/description/target
        # carry attacker-influenced text (e.g. a failed-login username).
        from apps.core.audit import csv_safe
        for r in qs:
            w.writerow([csv_safe(c) for c in (
                r.created_at.isoformat(), r.event_type, r.username, r.ip_address or "",
                f"{r.target_type} {r.target_id}".strip(), r.target_name,
                "yes" if r.success else "no", r.description, r.error_message,
            )])
        return resp

    @extend_schema(responses=None, summary="Audit log stats")
    @action(detail=False, methods=["get"])
    def stats(self, request):
        from datetime import timedelta

        from django.db.models import Count
        from django.utils import timezone

        from .models import AuditLog

        now = timezone.now()
        day_ago = now - timedelta(days=1)
        week_ago = now - timedelta(days=7)
        qs = AuditLog.objects.all()
        by_type = dict(
            qs.values_list("event_type").annotate(n=Count("id")).values_list("event_type", "n"))
        by_user = list(
            qs.exclude(username="").values("username").annotate(count=Count("id"))
            .order_by("-count")[:10])
        return Response({
            "today": qs.filter(created_at__gte=day_ago).count(),
            "this_week": qs.filter(created_at__gte=week_ago).count(),
            "by_event_type": by_type,
            "by_user": by_user,
            "failed_logins_24h": qs.filter(
                event_type=AuditLog.EventType.LOGIN_FAILED, created_at__gte=day_ago).count(),
        })


class AuditRetentionView(APIView):
    """Get/set how many days audit-log rows are kept (admin to change)."""

    def get_permissions(self):
        if self.request.method in SAFE_METHODS:
            return [IsAuthenticated()]
        return [HasCapability("rbac:manage")()]

    def _days(self) -> int:
        try:
            return int(SystemSetting.get("audit_log_retention_days",
                                         os.environ.get("AUDIT_LOG_RETENTION_DAYS", "90")))
        except (TypeError, ValueError):
            return 90

    @extend_schema(summary="Audit-log retention (days)", responses=None)
    def get(self, request):
        return Response({"audit_log_retention_days": self._days()})

    @extend_schema(summary="Set audit-log retention (days)", request=None, responses=None)
    def put(self, request):
        raw = request.data.get("audit_log_retention_days")
        try:
            days = int(raw)
        except (TypeError, ValueError):
            raise ValidationError({"audit_log_retention_days": "Must be an integer number of days."})
        if days < 0 or days > 3650:
            raise ValidationError({"audit_log_retention_days": "Must be between 0 and 3650."})
        SystemSetting.set("audit_log_retention_days", str(days))
        from .audit import log_event
        from .models import AuditLog
        log_event(AuditLog.EventType.SETTINGS_CHANGED, request=request,
                  description=f"Audit-log retention set to {days} days",
                  metadata={"audit_log_retention_days": days})
        return Response({"audit_log_retention_days": days})
