from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import (
    EmailSettingsView, EmailTestView, MistViewSet, NetBoxImportViewSet,
    UnifiControllerViewSet,
)

router = DefaultRouter()
router.register("netbox", NetBoxImportViewSet, basename="netbox-import")
router.register("unifi", UnifiControllerViewSet, basename="unifi-controller")

urlpatterns = [
    path("email/", EmailSettingsView.as_view(), name="email-settings"),
    path("email/test/", EmailTestView.as_view(), name="email-test"),
    # Juniper Mist (singleton cloud account; not a CRUD collection).
    path("mist/", MistViewSet.as_view({"get": "retrieve", "put": "update"}), name="mist"),
    path("mist/test/", MistViewSet.as_view({"post": "test"}), name="mist-test"),
    path("mist/sync/", MistViewSet.as_view({"post": "sync"}), name="mist-sync"),
    path("mist/sites/", MistViewSet.as_view({"get": "sites"}), name="mist-sites"),
    *router.urls,
]
