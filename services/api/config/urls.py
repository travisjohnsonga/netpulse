from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

urlpatterns = [
    path("admin/", admin.site.urls),
    # Core (health check)
    path("api/", include("apps.core.urls")),
    # Domain apps
    path("api/devices/", include("apps.devices.urls")),
    path("api/telemetry/", include("apps.telemetry.urls")),
    path("api/compliance/", include("apps.compliance.urls")),
    path("api/alerts/", include("apps.alerts.urls")),
    path("api/cve/", include("apps.cve.urls")),
    path("api/lifecycle/", include("apps.lifecycle.urls")),
    path("api/security/", include("apps.security.urls")),
    path("api/collectors/", include("apps.collectors.urls")),
    # OpenAPI
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),
]
