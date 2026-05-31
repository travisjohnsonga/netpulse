from rest_framework.routers import DefaultRouter

from django.urls import path

from .views import (
    AlertNotificationViewSet, AlertRouteViewSet, ContactMethodViewSet,
    EscalationPolicyViewSet, EscalationStepViewSet, MaintenanceWindowViewSet,
    OnCallScheduleViewSet, OnCallShiftViewSet, TeamViewSet,
)

router = DefaultRouter()
router.register("teams", TeamViewSet)
router.register("policies", EscalationPolicyViewSet)
router.register("steps", EscalationStepViewSet)
router.register("routes", AlertRouteViewSet)
router.register("contact-methods", ContactMethodViewSet)
router.register("notifications", AlertNotificationViewSet)
router.register("schedules", OnCallScheduleViewSet)
router.register("shifts", OnCallShiftViewSet)
router.register("maintenance", MaintenanceWindowViewSet)

# /api/alerting/on-call/ → who is on-call now (alias of schedules/current/).
urlpatterns = [
    path("on-call/", OnCallScheduleViewSet.as_view({"get": "current"}), name="alerting-on-call"),
] + router.urls
