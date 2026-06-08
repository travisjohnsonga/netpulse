"""
Rate-limited JWT auth views.

The token-obtain / refresh endpoints are unauthenticated and a brute-force
target, so they carry a scoped throttle ("auth" rate, keyed by client IP for
anonymous callers). The rest of the API is unthrottled (see REST_FRAMEWORK).

The obtain view also writes a login-success / login-failed audit record.
"""
from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.throttling import ScopedRateThrottle
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from .audit import log_event
from .models import AuditLog


class ThrottledTokenObtainPairView(TokenObtainPairView):
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth"

    def post(self, request, *args, **kwargs):
        username = (request.data or {}).get("username", "")
        try:
            response = super().post(request, *args, **kwargs)
        except Exception:
            # Bad credentials / inactive account → audit the failure, then re-raise.
            log_event(
                AuditLog.EventType.LOGIN_FAILED, request=request, username=username,
                description=f"Failed login for {username or '(unknown)'}", success=False,
            )
            raise
        if response.status_code == status.HTTP_200_OK:
            user = get_user_model().objects.filter(username=username).first()
            log_event(
                AuditLog.EventType.LOGIN_SUCCESS, request=request, user=user,
                username=username, description=f"User {username} logged in",
            )
        return response


class ThrottledTokenRefreshView(TokenRefreshView):
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth"
