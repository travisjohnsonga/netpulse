from rest_framework.routers import DefaultRouter

from .views import DeviceGroupViewSet, DeviceViewSet, SiteViewSet

router = DefaultRouter()
router.register("sites", SiteViewSet)
router.register("groups", DeviceGroupViewSet)
router.register("", DeviceViewSet)

urlpatterns = router.urls
