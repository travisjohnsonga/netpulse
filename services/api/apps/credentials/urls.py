from rest_framework.routers import DefaultRouter

from .views import CredentialProfileViewSet

router = DefaultRouter()
router.register("", CredentialProfileViewSet)

urlpatterns = router.urls
