from rest_framework.routers import DefaultRouter

from .views import (
    AlertChannelViewSet, AlertEventViewSet, AlertRuleViewSet, NotificationLogViewSet,
)

router = DefaultRouter()
router.register("channels", AlertChannelViewSet)
router.register("rules", AlertRuleViewSet)
router.register("events", AlertEventViewSet)
router.register("notifications", NotificationLogViewSet)

urlpatterns = router.urls
