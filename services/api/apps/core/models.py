from django.contrib.auth.models import AbstractUser
from django.db import models


class Role(models.TextChoices):
    ADMIN    = "admin",    "Admin"
    ENGINEER = "engineer", "Engineer"
    VIEWER   = "viewer",   "Viewer"
    API      = "api",      "API Service Account"


class NetPulseUser(AbstractUser):
    """
    Custom user model. Adds a role field used for RBAC throughout the API.

    Roles:
      admin    – full access to all endpoints and the Django admin panel
      engineer – read/write on all operational endpoints
      viewer   – read-only (safe HTTP methods only)
      api      – service-account tokens for integrations (read/write, no admin panel)
    """
    role = models.CharField(
        max_length=10,
        choices=Role.choices,
        default=Role.VIEWER,
        db_index=True,
    )

    class Meta(AbstractUser.Meta):
        swappable = "AUTH_USER_MODEL"

    def __str__(self):
        return f"{self.username} ({self.role})"

    @property
    def is_admin(self) -> bool:
        return self.is_superuser or self.role == Role.ADMIN

    @property
    def can_write(self) -> bool:
        return self.is_superuser or self.role in (Role.ADMIN, Role.ENGINEER, Role.API)


class TimestampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True
        ordering = ["-created_at"]
