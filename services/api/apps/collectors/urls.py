from rest_framework.routers import DefaultRouter

from .views import CollectorViewSet

router = DefaultRouter()
router.register("", CollectorViewSet)

urlpatterns = router.urls
