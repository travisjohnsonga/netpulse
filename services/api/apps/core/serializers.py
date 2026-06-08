"""Core serializers — custom JWT token payload, user profile & preferences."""
from django.contrib.auth import get_user_model, password_validation
from django.contrib.auth.models import Group
from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

from .models import Role, UserPreferences

User = get_user_model()

# Role value (lowercase) → Django auth Group name (seeded by create_roles).
ROLE_GROUP = {Role.ADMIN: "Admin", Role.ENGINEER: "Engineer", Role.VIEWER: "Viewer", Role.API: "API"}


def sync_role_group(user):
    """Make the user's auth Group membership match their role (Django-admin parity)."""
    user.groups.remove(*Group.objects.filter(name__in=ROLE_GROUP.values()))
    group = Group.objects.filter(name=ROLE_GROUP.get(user.role)).first()
    if group:
        user.groups.add(group)


class NetPulseTokenObtainPairSerializer(TokenObtainPairSerializer):
    """Extends the standard JWT access token with role, username + display claims."""

    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)
        token["username"] = user.username
        token["role"]     = user.role
        # Display claims so the UI can render a name/email/initials without an
        # extra request — and so SSO logins (which mint via this same method)
        # carry them too.
        token["email"]    = user.email or ""
        token["name"]     = f"{user.first_name} {user.last_name}".strip()
        # Drives the forced-password-change gate in the SPA (also returned in the
        # login response body below for convenience).
        token["must_change_password"] = bool(getattr(user, "must_change_password", False))
        return token

    def validate(self, attrs):
        data = super().validate(attrs)
        data["must_change_password"] = bool(getattr(self.user, "must_change_password", False))
        return data


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
    must_change_password = serializers.BooleanField(read_only=True)
    preferences = UserPreferencesSerializer(read_only=True)

    def update(self, instance, validated_data):
        for field in ("email", "first_name", "last_name"):
            if field in validated_data:
                setattr(instance, field, validated_data[field])
        instance.save()
        return instance


class AdminUserSerializer(serializers.ModelSerializer):
    """Admin-facing user management (Settings → Users). Password is write-only."""

    password = serializers.CharField(write_only=True, required=False, allow_blank=False)

    class Meta:
        model = User
        fields = (
            "id", "username", "email", "first_name", "last_name", "role",
            "is_active", "is_superuser", "last_login", "date_joined", "password",
        )
        read_only_fields = ("id", "is_superuser", "last_login", "date_joined")

    def validate_password(self, value):
        password_validation.validate_password(value)
        return value

    def create(self, validated_data):
        password = validated_data.pop("password", None)
        if not password:
            raise serializers.ValidationError({"password": "Password is required for a new user."})
        user = User(**validated_data)
        user.set_password(password)
        user.save()
        sync_role_group(user)
        return user

    def update(self, instance, validated_data):
        password = validated_data.pop("password", None)
        role_changed = "role" in validated_data and validated_data["role"] != instance.role
        for field, value in validated_data.items():
            setattr(instance, field, value)
        if password:
            instance.set_password(password)
        instance.save()
        if role_changed:
            sync_role_group(instance)
        return instance


# The fixed default password the initial admin is seeded with — must be changed
# on first login and may never be chosen as the new password.
DEFAULT_ADMIN_PASSWORD = "NetPulse1!"


class ChangePasswordSerializer(serializers.Serializer):
    current_password = serializers.CharField(write_only=True)
    new_password = serializers.CharField(write_only=True)

    def validate_current_password(self, value):
        user = self.context["request"].user
        if not user.check_password(value):
            raise serializers.ValidationError("Current password is incorrect.")
        return value

    def validate_new_password(self, value):
        # Explicit complexity rules (in addition to Django's configured
        # AUTH_PASSWORD_VALIDATORS): >= 8 chars, an uppercase letter and a digit.
        if len(value) < 8:
            raise serializers.ValidationError("Password must be at least 8 characters.")
        if not any(c.isupper() for c in value):
            raise serializers.ValidationError("Password must contain an uppercase letter.")
        if not any(c.isdigit() for c in value):
            raise serializers.ValidationError("Password must contain a number.")
        if value == DEFAULT_ADMIN_PASSWORD:
            raise serializers.ValidationError("Choose a password other than the default.")
        password_validation.validate_password(value, self.context["request"].user)
        return value

    def validate(self, attrs):
        if attrs.get("new_password") == attrs.get("current_password"):
            raise serializers.ValidationError(
                {"new_password": "New password must be different from the current password."})
        return attrs

    def save(self):
        user = self.context["request"].user
        user.set_password(self.validated_data["new_password"])
        # Clear the forced-change gate now that they've chosen their own password.
        user.must_change_password = False
        user.save(update_fields=["password", "must_change_password"])
        return user


class AuditLogSerializer(serializers.ModelSerializer):
    event_label = serializers.CharField(source="get_event_type_display", read_only=True)

    class Meta:
        from .models import AuditLog
        model = AuditLog
        fields = (
            "id", "event_type", "event_label", "user", "username", "ip_address",
            "user_agent", "target_type", "target_id", "target_name",
            "description", "metadata", "success", "error_message", "created_at",
        )
        read_only_fields = fields
