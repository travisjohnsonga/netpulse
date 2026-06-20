from rest_framework.routers import DefaultRouter

from .views import WanCircuitViewSet

router = DefaultRouter()
router.register("", WanCircuitViewSet, basename="circuit")

urlpatterns = [*router.urls]
