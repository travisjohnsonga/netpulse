from django.urls import path
from rest_framework.routers import DefaultRouter, SimpleRouter

from apps.telemetry.views import (
    DiscoverInterfacesView,
    GenerateConfigView,
    InterfaceAlertConfigView,
    InterfaceDeleteView,
    InterfaceListCreateView,
    PushConfigView,
    TelemetryConfigView,
)

from .views import (
    DeviceGroupViewSet,
    DeviceRoleViewSet,
    DeviceViewSet,
    DiscoveredDeviceViewSet,
    DiscoveryJobViewSet,
    HostnameRuleViewSet,
    SiteViewSet,
)

router = DefaultRouter()
router.register("sites", SiteViewSet)
router.register("groups", DeviceGroupViewSet)
router.register("roles", DeviceRoleViewSet)
router.register("hostname-rules", HostnameRuleViewSet)
router.register("", DeviceViewSet)

# Discovery routes live under /api/devices/discovery/{jobs,discovered}/. They are
# spread before the main router so the "" DeviceViewSet detail route does not
# swallow them.
disc_router = SimpleRouter()
disc_router.register("discovery/jobs", DiscoveryJobViewSet)
disc_router.register("discovery/discovered", DiscoveredDeviceViewSet)

# Device-scoped telemetry routes (declared before the router's catch-all detail
# route). if_name may contain slashes, so use the <path:> converter.
urlpatterns = [
    path("<int:device_id>/telemetry-config/", TelemetryConfigView.as_view(), name="device-telemetry-config"),
    path("<int:device_id>/telemetry-config/generate/", GenerateConfigView.as_view(), name="device-telemetry-generate"),
    path("<int:device_id>/telemetry-config/push/", PushConfigView.as_view(), name="device-telemetry-push"),
    path("<int:device_id>/interfaces/discover/", DiscoverInterfacesView.as_view(), name="device-interfaces-discover"),
    path("<int:device_id>/interfaces/alert-config/", InterfaceAlertConfigView.as_view(), name="device-interfaces-alert-config"),
    path("<int:device_id>/interfaces/", InterfaceListCreateView.as_view(), name="device-interfaces"),
    path("<int:device_id>/interfaces/<path:if_name>/", InterfaceDeleteView.as_view(), name="device-interface-delete"),
    *disc_router.urls,
    *router.urls,
]
