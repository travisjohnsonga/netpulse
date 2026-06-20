"""Topology routes mounted at /api/topology/ (manual link CRUD)."""
from rest_framework.routers import DefaultRouter

from .views import ManualTopologyLinkViewSet

router = DefaultRouter()
router.register("manual-links", ManualTopologyLinkViewSet, basename="manual-topology-link")

urlpatterns = [*router.urls]
