from django.conf import settings
from django.db import models

from apps.core.models import TimestampedModel


class CACertificate(TimestampedModel):
    """
    A trusted CA certificate added by an admin.

    Used to trust internal/private PKIs and SSL-inspection proxies for all of
    NetPulse's *outbound* HTTPS (CVE feeds, vendor APIs, git sync) and for
    nginx OCSP stapling. The PEM is public material, so it is stored in the DB
    as the source of truth; the on-disk ca-bundle.crt is rebuilt from these
    rows. The fingerprint is unique so the same cert can't be added twice.
    """

    name = models.CharField(max_length=255, help_text="Admin-friendly label")
    subject = models.CharField(max_length=512, blank=True)
    issuer = models.CharField(max_length=512, blank=True)
    fingerprint_sha256 = models.CharField(max_length=128, unique=True)
    not_before = models.DateTimeField(null=True, blank=True)
    not_after = models.DateTimeField(null=True, blank=True)
    cert_pem = models.TextField()
    file_path = models.CharField(max_length=512, blank=True)
    is_root = models.BooleanField(default=False)
    is_intermediate = models.BooleanField(default=False)
    added_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="ca_certificates",
    )

    class Meta:
        verbose_name = "CA certificate"
        verbose_name_plural = "CA certificates"
        ordering = ["subject"]

    def __str__(self):
        return self.name or self.subject or self.fingerprint_sha256


class ServerCertificate(TimestampedModel):
    """
    Metadata for NetPulse's own HTTPS server certificate (singleton, pk=1).

    This is NOT for network devices — it is the TLS cert nginx serves the web
    UI/API with. The private key NEVER lives in the database: it is written to
    SSL_DIR on disk (mode 0600) and never returned by the API. Only parsed,
    non-secret metadata is stored here for display and expiry tracking.
    """

    class Source(models.TextChoices):
        SELF_SIGNED = "self_signed", "Self-signed"
        CSR = "csr", "CA-signed (CSR)"
        UPLOADED = "uploaded", "Uploaded"

    common_name = models.CharField(max_length=255, blank=True)
    sans = models.JSONField(default=list, blank=True)
    issuer = models.CharField(max_length=512, blank=True)
    serial = models.CharField(max_length=128, blank=True)
    fingerprint_sha256 = models.CharField(max_length=128, blank=True)
    not_before = models.DateTimeField(null=True, blank=True)
    not_after = models.DateTimeField(null=True, blank=True)
    source = models.CharField(max_length=20, choices=Source.choices, blank=True)
    installed = models.BooleanField(default=False)

    class Meta:
        verbose_name = "server certificate"
        verbose_name_plural = "server certificate"

    def __str__(self):
        return f"server certificate ({self.common_name or 'none'})"

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj
