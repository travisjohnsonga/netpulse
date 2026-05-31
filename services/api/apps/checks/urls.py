from rest_framework.routers import DefaultRouter

from .views import CheckResultViewSet, ServiceCheckViewSet

router = DefaultRouter()
router.register("results", CheckResultViewSet, basename="checkresult")
router.register("", ServiceCheckViewSet, basename="servicecheck")

urlpatterns = router.urls
