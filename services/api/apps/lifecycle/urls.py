from rest_framework.routers import DefaultRouter

from .views import LifecycleMilestoneViewSet

router = DefaultRouter()
router.register("milestones", LifecycleMilestoneViewSet)

urlpatterns = router.urls
