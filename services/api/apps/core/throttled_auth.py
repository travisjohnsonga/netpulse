"""
Rate-limited JWT auth views.

The token-obtain / refresh endpoints are unauthenticated and a brute-force
target, so they carry a scoped throttle ("auth" rate, keyed by client IP for
anonymous callers). The rest of the API is unthrottled (see REST_FRAMEWORK).

The obtain view also enforces the **second factor** for MFA: a local password
account with MFA enabled gets a short-lived challenge (not a token) instead of a
JWT pair; a privileged local account without MFA is forced through enrollment.
SSO logins mint their JWT on a different path and are covered by provider MFA.
It writes the login-success / login-failed audit records.
"""
from rest_framework import status
from rest_framework.response import Response
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from . import mfa
from .audit import log_event
from .client_ip import TrustedProxyScopedRateThrottle
from .http import NoStoreResponseMixin
from .models import AuditLog


class ThrottledTokenObtainPairView(NoStoreResponseMixin, TokenObtainPairView):
    throttle_classes = [TrustedProxyScopedRateThrottle]
    throttle_scope = "auth"

    def post(self, request, *args, **kwargs):
        username = (request.data or {}).get("username", "")
        serializer = self.get_serializer(data=request.data)
        try:
            serializer.is_valid(raise_exception=True)
        except Exception:
            # Bad credentials / inactive account → audit the failure, then re-raise
            # (SimpleJWT turns this into a 401).
            log_event(
                AuditLog.EventType.LOGIN_FAILED, request=request, username=username,
                description=f"Failed login for {username or '(unknown)'}", success=False,
            )
            raise

        # Password auth passed. This endpoint is local-password only, so apply the
        # local TOTP MFA policy (SSO never reaches here).
        user = serializer.user
        state = mfa.evaluate_login_mfa(user)

        if state == "challenge":
            # Do NOT issue the JWT — return a single-purpose challenge instead.
            return Response(
                {"mfa_required": True, "methods": ["totp", "recovery_code"],
                 "challenge_token": mfa.make_challenge_token(user)},
                status=status.HTTP_200_OK,
            )

        if state == "enroll":
            # Privileged/required local account with no MFA yet → forced enrollment.
            # Restricted token authorizes ONLY the MFA setup/confirm endpoints.
            log_event(
                AuditLog.EventType.MFA_ENROLLMENT_FORCED, request=request, user=user,
                username=username,
                description=f"MFA enrollment required for {username} before access is granted",
            )
            return Response(
                {"mfa_enrollment_required": True,
                 "enrollment_token": mfa.make_enrollment_token(user),
                 "detail": "MFA is required for your account. Complete setup to continue."},
                status=status.HTTP_200_OK,
            )

        # No MFA required — issue the JWT pair as before.
        log_event(
            AuditLog.EventType.LOGIN_SUCCESS, request=request, user=user,
            username=username, description=f"User {username} logged in",
        )
        return Response(serializer.validated_data, status=status.HTTP_200_OK)


class ThrottledTokenRefreshView(NoStoreResponseMixin, TokenRefreshView):
    throttle_classes = [TrustedProxyScopedRateThrottle]
    throttle_scope = "auth"
