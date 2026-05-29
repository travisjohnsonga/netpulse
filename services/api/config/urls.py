from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView, TokenVerifyView

urlpatterns = [
    path("admin/", admin.site.urls),

    # ── Authentication ────────────────────────────────────────────────────────
    path("api/auth/token/",         TokenObtainPairView.as_view(),  name="token-obtain"),
    path("api/auth/token/refresh/", TokenRefreshView.as_view(),     name="token-refresh"),
    path("api/auth/token/verify/",  TokenVerifyView.as_view(),      name="token-verify"),

    # ── Core (health check, chatops webhooks) ─────────────────────────────────
    path("api/", include("apps.core.urls")),

    # ── Domain apps ───────────────────────────────────────────────────────────
    path("api/devices/",    include("apps.devices.urls")),
    path("api/telemetry/",  include("apps.telemetry.urls")),
    path("api/compliance/", include("apps.compliance.urls")),
    path("api/alerts/",     include("apps.alerts.urls")),
    path("api/cve/",        include("apps.cve.urls")),
    path("api/lifecycle/",  include("apps.lifecycle.urls")),
    path("api/security/",   include("apps.security.urls")),
    path("api/collectors/", include("apps.collectors.urls")),

    # ── OpenAPI ───────────────────────────────────────────────────────────────
    path("api/schema/", SpectacularAPIView.as_view(),                 name="schema"),
    path("api/docs/",   SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),
]
