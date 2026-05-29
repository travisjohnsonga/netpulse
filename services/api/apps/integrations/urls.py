from rest_framework.routers import DefaultRouter

from .views import NetBoxImportViewSet

router = DefaultRouter()
router.register("netbox", NetBoxImportViewSet, basename="netbox-import")

urlpatterns = router.urls
