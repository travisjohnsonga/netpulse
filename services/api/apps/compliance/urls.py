from rest_framework.routers import DefaultRouter

from .views import CompliancePolicyRuleViewSet, CompliancePolicyViewSet, ComplianceResultViewSet

router = DefaultRouter()
router.register("policies", CompliancePolicyViewSet)
router.register("rules", CompliancePolicyRuleViewSet)
router.register("results", ComplianceResultViewSet)

urlpatterns = router.urls
