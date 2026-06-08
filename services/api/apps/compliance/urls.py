from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import (
    ApprovedOSVersionViewSet,
    ComplianceCheckView,
    CompliancePolicyRuleViewSet,
    CompliancePolicyViewSet,
    ComplianceResultViewSet,
    ComplianceTemplateResultViewSet,
    ComplianceTemplateViewSet,
    DiscoveredPlatformModelViewSet,
    OSComplianceSummaryView,
)

router = DefaultRouter()
router.register("policies", CompliancePolicyViewSet)
router.register("rules", CompliancePolicyRuleViewSet)
router.register("results", ComplianceResultViewSet)
router.register("templates", ComplianceTemplateViewSet)
router.register("template-results", ComplianceTemplateResultViewSet)
router.register("os-versions", ApprovedOSVersionViewSet)
router.register("discovered-platforms", DiscoveredPlatformModelViewSet)

urlpatterns = [
    path("check/", ComplianceCheckView.as_view(), name="compliance-check"),
    path("os-summary/", OSComplianceSummaryView.as_view(), name="compliance-os-summary"),
    *router.urls,
]
