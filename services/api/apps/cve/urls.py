from rest_framework.routers import DefaultRouter

from .views import CVEViewSet, DeviceCVEViewSet

router = DefaultRouter()
router.register("cves", CVEViewSet)
router.register("device-cves", DeviceCVEViewSet)

urlpatterns = router.urls
