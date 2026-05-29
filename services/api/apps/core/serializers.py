"""Core serializers — custom JWT token payload."""
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer


class NetPulseTokenObtainPairSerializer(TokenObtainPairSerializer):
    """Extends the standard JWT access token with role and username claims."""

    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)
        token["username"] = user.username
        token["role"]     = user.role
        return token
