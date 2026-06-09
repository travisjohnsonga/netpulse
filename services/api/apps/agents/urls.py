from rest_framework.routers import DefaultRouter

from .views import AgentEnrollmentTokenViewSet, AgentViewSet, ServerRoleViewSet

router = DefaultRouter()
# Register specific prefixes BEFORE the catch-all "" so /tokens/ and /roles/
# aren't captured by the agent detail route (pk pattern matches any string).
router.register("tokens", AgentEnrollmentTokenViewSet, basename="agent-token")
router.register("roles", ServerRoleViewSet, basename="server-role")
router.register("", AgentViewSet, basename="agent")

urlpatterns = router.urls
