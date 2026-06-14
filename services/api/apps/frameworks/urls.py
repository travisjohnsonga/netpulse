from rest_framework.routers import DefaultRouter

from .views import RegulatoryFrameworkViewSet

router = DefaultRouter()
router.register("frameworks", RegulatoryFrameworkViewSet, basename="framework")

urlpatterns = router.urls
