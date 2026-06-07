from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import EmailSettingsView, EmailTestView, NetBoxImportViewSet

router = DefaultRouter()
router.register("netbox", NetBoxImportViewSet, basename="netbox-import")

urlpatterns = [
    path("email/", EmailSettingsView.as_view(), name="email-settings"),
    path("email/test/", EmailTestView.as_view(), name="email-test"),
    *router.urls,
]
