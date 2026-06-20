from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import (
    ApprovedOSVersionViewSet,
    ComplianceCheckView,
    CompliancePolicyRuleViewSet,
    CompliancePolicyViewSet,
    ComplianceResultViewSet,
    ComplianceRunAllStatusView,
    ComplianceRunAllView,
    ComplianceRunDeviceView,
    ComplianceTemplateResultViewSet,
    ComplianceTemplateViewSet,
    DiscoveredPlatformModelViewSet,
    InterfaceComplianceResultViewSet,
    InterfaceComplianceRuleViewSet,
    OSComplianceSummaryView,
    RoleConsistencyRuleViewSet,
)

router = DefaultRouter()
router.register("policies", CompliancePolicyViewSet)
router.register("rules", CompliancePolicyRuleViewSet)
router.register("results", ComplianceResultViewSet)
router.register("templates", ComplianceTemplateViewSet)
router.register("template-results", ComplianceTemplateResultViewSet)
router.register("os-versions", ApprovedOSVersionViewSet)
router.register("discovered-platforms", DiscoveredPlatformModelViewSet)
router.register("interface-rules", InterfaceComplianceRuleViewSet, basename="interface-rule")
router.register("interface-results", InterfaceComplianceResultViewSet, basename="interface-result")
router.register("role-rules", RoleConsistencyRuleViewSet, basename="role-rule")

urlpatterns = [
    path("check/", ComplianceCheckView.as_view(), name="compliance-check"),
    path("os-summary/", OSComplianceSummaryView.as_view(), name="compliance-os-summary"),
    path("run-all/", ComplianceRunAllView.as_view(), name="compliance-run-all"),
    path("run-all/status/", ComplianceRunAllStatusView.as_view(), name="compliance-run-all-status"),
    path("run/<int:device_id>/", ComplianceRunDeviceView.as_view(), name="compliance-run-device"),
    *router.urls,
]
