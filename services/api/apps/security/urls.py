from rest_framework.routers import DefaultRouter

from .views import DeviceRiskScoreViewSet

router = DefaultRouter()
router.register("risk-scores", DeviceRiskScoreViewSet)

urlpatterns = router.urls
