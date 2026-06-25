"""MFA API: enrollment (setup / confirm / disable / status), the login
second-factor exchange, and the restricted forced-enrollment path.

Token scopes enforced here:

* The login *challenge* token is redeemable only at ``MFATokenView`` for a real
  JWT pair — it authenticates nothing.
* The forced *enrollment* token is honored only by ``MFASetupView`` /
  ``MFAConfirmView`` (they resolve the acting user from it). Every other endpoint
  uses the default ``JWTAuthentication``, which rejects it — so it grants no
  access scope and cannot be refreshed into a JWT.
"""
from django.contrib.auth import get_user_model
from django.core import signing
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import AuthenticationFailed, NotAuthenticated, ValidationError
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.authentication import JWTAuthentication

from . import mfa
from .audit import log_event
from .client_ip import TrustedProxyScopedRateThrottle
from .models import AuditLog, MFADevice
from .serializers import NetPulseTokenObtainPairSerializer

User = get_user_model()


def _issue_jwt(user) -> dict:
    """Mint the same access+refresh pair as a normal local/SSO login."""
    refresh = NetPulseTokenObtainPairSerializer.get_token(user)
    return {
        "access": str(refresh.access_token),
        "refresh": str(refresh),
        "must_change_password": bool(getattr(user, "must_change_password", False)),
    }


def _resolve_enrolling_user(request):
    """Return ``(user, forced)`` for the setup/confirm endpoints.

    A normally-authenticated user (opt-in enrollment) is taken from
    ``request.user``. Otherwise a ``mfa-enrollment`` intermediate token (forced
    enrollment of a privileged local user) is accepted from the body or the
    ``X-MFA-Enrollment-Token`` header — and ONLY here, so it reaches no other
    endpoint."""
    if request.user and request.user.is_authenticated:
        return request.user, False
    token = (request.data or {}).get("enrollment_token") or request.META.get(
        "HTTP_X_MFA_ENROLLMENT_TOKEN")
    if token:
        try:
            uid = mfa.load_enrollment_token(token)
        except signing.BadSignature:
            raise AuthenticationFailed("Invalid or expired enrollment token.")
        user = User.objects.filter(pk=uid, is_active=True).first()
        if user:
            return user, True
    raise NotAuthenticated("Authentication or a valid MFA enrollment token is required.")


class MFASetupView(APIView):
    """POST /api/auth/mfa/setup/ — generate a PENDING TOTP secret and return its
    provisioning URI + QR. Idempotent until confirmed. AllowAny because a forced
    enrollee has no JWT yet; the user is resolved from the JWT or the enrollment
    token."""

    # JWT-only (no SessionAuthentication): the voluntary path carries a Bearer
    # JWT, the forced-enrollment path carries the X-MFA-Enrollment-Token header.
    # Including SessionAuthentication would enforce CSRF for any anonymous request
    # that arrives with an authenticated session cookie — breaking forced
    # enrollment (no Bearer, no CSRF token). The login token endpoints avoid this
    # the same way (SimpleJWT TokenViewBase runs no authenticators). The
    # enrollment-token gating in _resolve_enrolling_user is unchanged, so this
    # removes CSRF, NOT the auth gate.
    authentication_classes = [JWTAuthentication]
    permission_classes = [AllowAny]

    def post(self, request):
        user, _forced = _resolve_enrolling_user(request)
        device = getattr(user, "mfa_device", None)
        if device and device.mfa_enabled:
            raise ValidationError("MFA is already enabled. Disable it before re-enrolling.")
        if device is None:
            device = MFADevice(user=user)
        secret = mfa.generate_secret()
        device.set_secret(secret)
        device.mfa_enabled = False
        device.confirmed_at = None
        device.last_step = None
        device.recovery_codes = []
        device.save()
        uri = mfa.provisioning_uri(secret, account_name=user.username)
        return Response({
            "otpauth_uri": uri,
            "qr_code": mfa.qr_data_uri(uri),
            # The base32 secret for manual entry. Returned ONLY during pending
            # setup (before confirmation); never returned once active.
            "secret": secret,
        })


class MFAConfirmView(APIView):
    """POST /api/auth/mfa/confirm/ {code} — verify the pending secret, activate
    MFA, and return one-time recovery codes (shown ONCE). On the forced-enrollment
    path it also issues the real JWT pair so the user is logged in."""

    # JWT-only — see MFASetupView (no SessionAuthentication/CSRF; the enrollment
    # token gate in _resolve_enrolling_user is preserved).
    authentication_classes = [JWTAuthentication]
    permission_classes = [AllowAny]

    def post(self, request):
        user, forced = _resolve_enrolling_user(request)
        device = getattr(user, "mfa_device", None)
        if device is None or device.mfa_enabled or not device.get_secret():
            raise ValidationError("No pending MFA setup. Call /api/auth/mfa/setup/ first.")
        code = (request.data or {}).get("code", "")
        # record=False: don't consume the step — the user will log in moments later
        # with a code from the same 30s window; the login second factor enforces
        # the replay guard from then on.
        if not device.verify_totp(code, record=False):
            log_event(AuditLog.EventType.MFA_FAILED, request=request, user=user,
                      username=user.username, success=False,
                      description=f"MFA confirm failed for {user.username}")
            raise ValidationError("Invalid code. Re-scan and try again.")
        recovery = mfa.generate_recovery_codes()
        device.set_recovery_codes(recovery)
        device.mfa_enabled = True
        device.confirmed_at = timezone.now()
        device.save()
        log_event(AuditLog.EventType.MFA_ENABLED, request=request, user=user,
                  username=user.username, target=user,
                  description=f"MFA enabled for {user.username}")
        if forced:
            log_event(AuditLog.EventType.MFA_ENROLLMENT_COMPLETED, request=request, user=user,
                      username=user.username, description=f"Forced MFA enrollment completed for {user.username}")
        body = {"recovery_codes": recovery, "mfa_enabled": True}
        if forced:
            # Forced enrollee had no full token — issue it now that MFA is active.
            body["tokens"] = _issue_jwt(user)
            log_event(AuditLog.EventType.LOGIN_SUCCESS, request=request, user=user,
                      username=user.username, description=f"User {user.username} logged in (post-enrollment)")
        return Response(body)


class MFADisableView(APIView):
    """POST /api/auth/mfa/disable/ {code} — turn off MFA. Requires a valid TOTP
    (or recovery) code from the authenticated owner, so a hijacked session can't
    silently strip the second factor."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        user = request.user
        device = getattr(user, "mfa_device", None)
        if device is None or not device.mfa_enabled:
            raise ValidationError("MFA is not enabled.")
        code = (request.data or {}).get("code", "")
        if not (device.verify_totp(code) or device.verify_recovery(code)):
            log_event(AuditLog.EventType.MFA_FAILED, request=request, user=user,
                      username=user.username, success=False,
                      description=f"MFA disable failed for {user.username}")
            raise ValidationError("A valid current code is required to disable MFA.")
        device.clear()
        device.save()
        log_event(AuditLog.EventType.MFA_DISABLED, request=request, user=user,
                  username=user.username, target=user,
                  description=f"MFA disabled for {user.username}")
        return Response({"mfa_enabled": False})


class MFAStatusView(APIView):
    """GET /api/auth/mfa/ — the current user's MFA status (never the secret)."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        device = getattr(request.user, "mfa_device", None)
        return Response({
            "mfa_enabled": bool(device and device.mfa_enabled),
            "confirmed_at": device.confirmed_at if device else None,
            "recovery_codes_remaining": device.recovery_codes_remaining if device else 0,
            "required": mfa.mfa_required_for(request.user),
        })


class MFATokenView(APIView):
    """POST /api/auth/token/mfa/ {challenge_token, code|recovery_code} — the login
    second factor. Redeems a challenge token for the real JWT pair. Throttled on
    the same ``auth`` scope as the password endpoint (counts toward lockout)."""

    # No authenticators — purely pre-JWT, exactly like the SimpleJWT token
    # endpoints (TokenViewBase). Avoids SessionAuthentication/CSRF; the
    # challenge_token is validated in the view below.
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [TrustedProxyScopedRateThrottle]
    throttle_scope = "auth"

    def post(self, request):
        data = request.data or {}
        token = data.get("challenge_token", "")
        try:
            uid = mfa.load_challenge_token(token)
        except signing.BadSignature:
            raise AuthenticationFailed("Invalid or expired MFA challenge. Sign in again.")
        user = User.objects.filter(pk=uid, is_active=True).first()
        device = getattr(user, "mfa_device", None) if user else None
        if user is None or device is None or not device.mfa_enabled:
            raise AuthenticationFailed("Invalid MFA challenge.")

        code = data.get("code", "")
        recovery = data.get("recovery_code", "")
        ok = device.verify_totp(code) if code else (device.verify_recovery(recovery) if recovery else False)
        if not ok:
            device.save()  # persist nothing sensitive; keeps last_step if TOTP advanced
            log_event(AuditLog.EventType.MFA_FAILED, request=request, user=user,
                      username=user.username, success=False,
                      description=f"MFA code rejected for {user.username}")
            log_event(AuditLog.EventType.LOGIN_FAILED, request=request, user=user,
                      username=user.username, success=False,
                      description=f"Second factor failed for {user.username}")
            return Response({"detail": "Invalid MFA code."}, status=status.HTTP_403_FORBIDDEN)

        device.save()  # persist last_step / consumed recovery code
        log_event(AuditLog.EventType.LOGIN_SUCCESS, request=request, user=user,
                  username=user.username, description=f"User {user.username} logged in (MFA)")
        return Response(_issue_jwt(user))
