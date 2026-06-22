from rest_framework.routers import DefaultRouter

from .views import ConfigPushTemplateViewSet

router = DefaultRouter()
router.register("", ConfigPushTemplateViewSet, basename="config-template")

urlpatterns = [*router.urls]
