from django.urls import path
from rest_framework.routers import DefaultRouter

from apps.credentials.views import (
    DeviceCredentialListCreateView,
    DeviceCredentialPurposeView,
)

from .views import DeviceGroupViewSet, DeviceViewSet, SiteViewSet

router = DefaultRouter()
router.register("sites", SiteViewSet)
router.register("groups", DeviceGroupViewSet)
router.register("", DeviceViewSet)

# Device-scoped credential associations. Declared before the router's catch-all
# detail route so they resolve cleanly.
urlpatterns = [
    path("<int:device_id>/credentials/",
         DeviceCredentialListCreateView.as_view(), name="device-credentials"),
    path("<int:device_id>/credentials/<str:purpose>/",
         DeviceCredentialPurposeView.as_view(), name="device-credential-purpose"),
    *router.urls,
]
