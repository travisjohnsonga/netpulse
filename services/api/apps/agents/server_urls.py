"""Routes for the Servers API (`/api/servers/`)."""
from rest_framework.routers import DefaultRouter

from .server_views import ServerViewSet

router = DefaultRouter()
router.register("", ServerViewSet, basename="server")

urlpatterns = router.urls
