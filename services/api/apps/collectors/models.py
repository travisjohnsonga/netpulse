from django.db import models

from apps.core.models import TimestampedModel


class Collector(TimestampedModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending Registration"
        ACTIVE = "active", "Active"
        OFFLINE = "offline", "Offline"
        REVOKED = "revoked", "Revoked"

    name = models.CharField(max_length=255)
    # bcrypt hash of the API key issued at registration; never stored in plaintext
    api_key_hash = models.CharField(max_length=128, unique=True)
    # OpenBao PKI serial for the collector's mTLS certificate
    cert_serial = models.CharField(max_length=128, blank=True)
    cert_expires_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING, db_index=True)
    last_seen_at = models.DateTimeField(null=True, blank=True)
    version = models.CharField(max_length=50, blank=True)
    remote_ip = models.GenericIPAddressField(null=True, blank=True)

    def __str__(self):
        return self.name
