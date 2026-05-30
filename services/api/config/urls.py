from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularRedocView, SpectacularSwaggerView
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView, TokenVerifyView

from apps.core.views import SystemSettingsView
from apps.devices.views import SiteViewSet
from apps.telemetry.views import PollingSettingsView

# Top-level sites router (also available under /api/devices/sites/).
_sites_router = DefaultRouter()
_sites_router.register("", SiteViewSet, basename="site-top")

urlpatterns = [
    path("admin/", admin.site.urls),

    # ── Authentication ────────────────────────────────────────────────────────
    path("api/auth/token/",         TokenObtainPairView.as_view(),  name="token-obtain"),
    path("api/auth/token/refresh/", TokenRefreshView.as_view(),     name="token-refresh"),
    path("api/auth/token/verify/",  TokenVerifyView.as_view(),      name="token-verify"),

    # ── Core (health check, chatops webhooks) ─────────────────────────────────
    path("api/", include("apps.core.urls")),

    # ── Domain apps ───────────────────────────────────────────────────────────
    path("api/devices/",     include("apps.devices.urls")),
    path("api/sites/",       include((_sites_router.urls, "sites"))),
    path("api/credentials/", include("apps.credentials.urls")),
    path("api/telemetry/",   include("apps.telemetry.urls")),
    path("api/compliance/", include("apps.compliance.urls")),
    path("api/alerts/",     include("apps.alerts.urls")),
    path("api/cve/",        include("apps.cve.urls")),
    path("api/lifecycle/",  include("apps.lifecycle.urls")),
    path("api/security/",   include("apps.security.urls")),
    path("api/collectors/", include("apps.collectors.urls")),
    path("api/import/",      include("apps.integrations.urls")),
    path("api/settings/",     include("apps.configbackup.urls")),
    path("api/configbackup/",  include("apps.configbackup.urls")),
    path("api/logs/",         include("apps.logs.urls")),
    path("api/settings/polling/", PollingSettingsView.as_view(), name="polling-settings"),
    path("api/settings/system/",  SystemSettingsView.as_view(),  name="system-settings"),
    path("api/settings/",      include("apps.tls.urls")),

    # ── OpenAPI ───────────────────────────────────────────────────────────────
    path("api/schema/", SpectacularAPIView.as_view(),                      name="schema"),
    path("api/docs/",   SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),
    path("api/redoc/",  SpectacularRedocView.as_view(url_name="schema"),   name="redoc"),
]
