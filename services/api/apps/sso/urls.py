from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import SSOProviderViewSet, sso_jwt_redirect

router = DefaultRouter()
router.register("providers", SSOProviderViewSet, basename="sso-provider")

urlpatterns = router.urls + [
    # Where social-auth lands after a successful login (SOCIAL_AUTH_LOGIN_REDIRECT_URL).
    path("jwt/", sso_jwt_redirect, name="sso-jwt"),
]
