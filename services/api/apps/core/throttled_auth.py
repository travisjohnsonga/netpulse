"""
Rate-limited JWT auth views.

The token-obtain / refresh endpoints are unauthenticated and a brute-force
target, so they carry a scoped throttle ("auth" rate, keyed by client IP for
anonymous callers). The rest of the API is unthrottled (see REST_FRAMEWORK).
"""
from rest_framework.throttling import ScopedRateThrottle
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView


class ThrottledTokenObtainPairView(TokenObtainPairView):
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth"


class ThrottledTokenRefreshView(TokenRefreshView):
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth"
