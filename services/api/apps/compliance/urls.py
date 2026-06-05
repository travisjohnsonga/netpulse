from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import (
    ComplianceCheckView,
    CompliancePolicyRuleViewSet,
    CompliancePolicyViewSet,
    ComplianceResultViewSet,
    ComplianceTemplateResultViewSet,
    ComplianceTemplateViewSet,
)

router = DefaultRouter()
router.register("policies", CompliancePolicyViewSet)
router.register("rules", CompliancePolicyRuleViewSet)
router.register("results", ComplianceResultViewSet)
router.register("templates", ComplianceTemplateViewSet)
router.register("template-results", ComplianceTemplateResultViewSet)

urlpatterns = [
    path("check/", ComplianceCheckView.as_view(), name="compliance-check"),
    *router.urls,
]
