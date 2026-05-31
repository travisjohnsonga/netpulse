from rest_framework.routers import DefaultRouter

from .views import (
    AlertNotificationViewSet, AlertRouteViewSet, ContactMethodViewSet,
    EscalationPolicyViewSet, EscalationStepViewSet, TeamViewSet,
)

router = DefaultRouter()
router.register("teams", TeamViewSet)
router.register("policies", EscalationPolicyViewSet)
router.register("steps", EscalationStepViewSet)
router.register("routes", AlertRouteViewSet)
router.register("contact-methods", ContactMethodViewSet)
router.register("notifications", AlertNotificationViewSet)

urlpatterns = router.urls
