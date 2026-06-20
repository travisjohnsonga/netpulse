from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import (
    ChatOpsChannelViewSet, ChatOpsConfigView, ChatOpsIdentityViewSet,
    ChatOpsPlatformViewSet,
)

router = DefaultRouter()
router.register("channels", ChatOpsChannelViewSet, basename="chatops-channel")
router.register("identities", ChatOpsIdentityViewSet, basename="chatops-identity")

urlpatterns = [
    # Per-platform config (one row per platform; keyed by platform slug).
    path("platforms/", ChatOpsPlatformViewSet.as_view({"get": "list"}),
         name="chatops-platforms"),
    path("platforms/<str:platform>/",
         ChatOpsPlatformViewSet.as_view({"get": "retrieve", "put": "update"}),
         name="chatops-platform"),
    path("platforms/<str:platform>/test/",
         ChatOpsPlatformViewSet.as_view({"post": "test"}),
         name="chatops-platform-test"),
    # Singleton global policy.
    path("config/", ChatOpsConfigView.as_view(), name="chatops-config"),
    *router.urls,
]
