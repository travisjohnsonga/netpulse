from rest_framework.routers import DefaultRouter

from .views import AlertChannelViewSet, AlertEventViewSet, AlertRuleViewSet

router = DefaultRouter()
router.register("channels", AlertChannelViewSet)
router.register("rules", AlertRuleViewSet)
router.register("events", AlertEventViewSet)

urlpatterns = router.urls
