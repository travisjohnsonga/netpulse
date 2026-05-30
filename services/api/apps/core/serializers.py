"""Core serializers — custom JWT token payload, user profile & preferences."""
from django.contrib.auth import password_validation
from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

from .models import UserPreferences


class NetPulseTokenObtainPairSerializer(TokenObtainPairSerializer):
    """Extends the standard JWT access token with role and username claims."""

    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)
        token["username"] = user.username
        token["role"]     = user.role
        return token


class UserPreferencesSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserPreferences
        exclude = ("id", "user")
        read_only_fields = ("created_at", "updated_at")


class MeSerializer(serializers.Serializer):
    """Current user's account info + nested preferences."""

    username = serializers.CharField(read_only=True)
    email = serializers.EmailField(required=False, allow_blank=True)
    first_name = serializers.CharField(required=False, allow_blank=True)
    last_name = serializers.CharField(required=False, allow_blank=True)
    role = serializers.CharField(read_only=True)
    is_superuser = serializers.BooleanField(read_only=True)
    preferences = UserPreferencesSerializer(read_only=True)

    def update(self, instance, validated_data):
        for field in ("email", "first_name", "last_name"):
            if field in validated_data:
                setattr(instance, field, validated_data[field])
        instance.save()
        return instance


class ChangePasswordSerializer(serializers.Serializer):
    current_password = serializers.CharField(write_only=True)
    new_password = serializers.CharField(write_only=True)

    def validate_current_password(self, value):
        user = self.context["request"].user
        if not user.check_password(value):
            raise serializers.ValidationError("Current password is incorrect.")
        return value

    def validate_new_password(self, value):
        password_validation.validate_password(value, self.context["request"].user)
        return value

    def save(self):
        user = self.context["request"].user
        user.set_password(self.validated_data["new_password"])
        user.save(update_fields=["password"])
        return user
