from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularRedocView, SpectacularSwaggerView
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenVerifyView

from apps.core.throttled_auth import ThrottledTokenObtainPairView, ThrottledTokenRefreshView
from apps.core.views import HostnameDisplayView, SystemSettingsView
from apps.devices.views import SiteViewSet
from apps.telemetry.views import PollingSettingsView

# Top-level sites router (also available under /api/devices/sites/).
_sites_router = DefaultRouter()
_sites_router.register("", SiteViewSet, basename="site-top")

urlpatterns = [
    path("admin/", admin.site.urls),

    # ── Authentication ────────────────────────────────────────────────────────
    path("api/auth/token/",         ThrottledTokenObtainPairView.as_view(),  name="token-obtain"),
    path("api/auth/token/refresh/", ThrottledTokenRefreshView.as_view(),     name="token-refresh"),
    path("api/auth/token/verify/",  TokenVerifyView.as_view(),      name="token-verify"),

    # ── SSO (Single Sign-On) ──────────────────────────────────────────────────
    path("api/sso/", include("apps.sso.urls")),
    # social-auth begin/complete/disconnect (/auth/login/<backend>/ etc.)
    path("auth/", include("social_django.urls", namespace="social")),

    # ── Core (health check, chatops webhooks) ─────────────────────────────────
    path("api/", include("apps.core.urls")),

    # ── Domain apps ───────────────────────────────────────────────────────────
    # ARP/MAC before the devices router so /api/devices/<id>/arp/ etc. resolve
    # to the explicit views rather than the DeviceViewSet detail routes.
    path("api/", include("apps.arp_mac.urls")),
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
    path("api/settings/",     include("apps.tls.urls")),
    path("api/settings/",     include("apps.configbackup.urls")),
    path("api/configbackup/",  include("apps.configbackup.urls")),
    path("api/logs/",         include("apps.logs.urls")),
    path("api/flows/",        include("apps.flows.urls")),
    path("api/checks/",       include("apps.checks.urls")),
    path("api/alerting/",     include("apps.alerting.urls")),
    path("api/mibs/",         include("apps.mibs.urls")),
    path("api/settings/polling/", PollingSettingsView.as_view(), name="polling-settings"),
    path("api/settings/system/",  SystemSettingsView.as_view(),  name="system-settings"),
    path("api/settings/hostname-display/", HostnameDisplayView.as_view(), name="hostname-display"),

    # ── OpenAPI ───────────────────────────────────────────────────────────────
    path("api/schema/", SpectacularAPIView.as_view(),                      name="schema"),
    path("api/docs/",   SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),
    path("api/redoc/",  SpectacularRedocView.as_view(url_name="schema"),   name="redoc"),
]
