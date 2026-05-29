"""
Credential profile models.

A CredentialProfile holds only *metadata* about how to authenticate to a
device (type, username, SNMP/SSH/TLS parameters, port, …).  The actual
secret material — passwords, SSH private keys, SNMP community strings, API
tokens — is **never** stored in PostgreSQL.  It lives in OpenBao at
``vault_path``; this database only keeps the path.  See ``apps.credentials.vault``.

Devices are linked to profiles through :class:`DeviceCredential`, which records
*why* a device uses a profile (the ``purpose``) plus per-device usage stats.
"""
from django.contrib.auth import get_user_model
from django.db import models

from apps.core.models import TimestampedModel


class CredentialProfile(TimestampedModel):
    """A reusable set of authentication parameters, secrets held in OpenBao."""

    class CredentialType(models.TextChoices):
        SNMPV1       = "snmpv1",       "SNMP v1"
        SNMPV2C      = "snmpv2c",      "SNMP v2c"
        SNMPV3       = "snmpv3",       "SNMP v3"
        SSH_PASSWORD = "ssh_password", "SSH (password)"
        SSH_KEY      = "ssh_key",      "SSH (key)"
        HTTP_BASIC   = "http_basic",   "HTTP Basic Auth"
        HTTP_TOKEN   = "http_token",   "HTTP Bearer Token"
        HTTP_APIKEY  = "http_apikey",  "HTTP API Key"
        GNMI         = "gnmi",         "gNMI"
        NETCONF      = "netconf",      "NETCONF"

    class SNMPVersion(models.TextChoices):
        V1  = "1",  "v1"
        V2C = "2c", "v2c"
        V3  = "3",  "v3"

    class SNMPSecurityLevel(models.TextChoices):
        NO_AUTH_NO_PRIV = "noAuthNoPriv", "noAuthNoPriv"
        AUTH_NO_PRIV    = "authNoPriv",   "authNoPriv"
        AUTH_PRIV       = "authPriv",     "authPriv"

    class AuthMethod(models.TextChoices):
        PASSWORD = "password", "Password"
        KEY      = "key",      "SSH Key"
        TOKEN    = "token",    "Token"
        APIKEY   = "apikey",   "API Key"
        COMMUNITY = "community", "Community String"

    class TestResult(models.TextChoices):
        UNTESTED = "untested", "Untested"
        SUCCESS  = "success",  "Success"
        FAILURE  = "failure",  "Failure"

    name = models.CharField(max_length=255, unique=True, db_index=True)
    credential_type = models.CharField(
        max_length=20, choices=CredentialType.choices, db_index=True
    )
    description = models.TextField(blank=True)
    # OpenBao KV path where the secret material lives. Auto-derived from the pk
    # on first save; the actual secrets are never persisted in this table.
    vault_path = models.CharField(max_length=512, blank=True)

    # ── Common auth parameters ────────────────────────────────────────────────
    username = models.CharField(max_length=255, blank=True)
    auth_method = models.CharField(
        max_length=20, choices=AuthMethod.choices, blank=True
    )
    port = models.PositiveIntegerField(null=True, blank=True)
    tls_enabled = models.BooleanField(default=False)

    # ── SNMP-specific ─────────────────────────────────────────────────────────
    snmp_version = models.CharField(
        max_length=4, choices=SNMPVersion.choices, blank=True
    )
    snmp_security_level = models.CharField(
        max_length=16, choices=SNMPSecurityLevel.choices, blank=True
    )
    auth_protocol = models.CharField(max_length=16, blank=True)  # SHA, MD5, SHA256…
    priv_protocol = models.CharField(max_length=16, blank=True)  # AES, DES, AES256…

    # ── Audit / test bookkeeping ──────────────────────────────────────────────
    created_by = models.ForeignKey(
        get_user_model(), null=True, blank=True,
        on_delete=models.SET_NULL, related_name="credential_profiles",
    )
    last_tested = models.DateTimeField(null=True, blank=True)
    last_test_result = models.CharField(
        max_length=10, choices=TestResult.choices, default=TestResult.UNTESTED
    )
    last_test_message = models.TextField(blank=True)

    class Meta(TimestampedModel.Meta):
        indexes = [models.Index(fields=["credential_type", "name"])]

    def __str__(self):
        return f"{self.name} ({self.credential_type})"

    def default_vault_path(self) -> str:
        """Deterministic OpenBao path for this profile's secret material."""
        return f"netpulse/credentials/{self.pk}"

    @property
    def device_count(self) -> int:
        return self.device_links.count()


class DeviceCredential(TimestampedModel):
    """
    Through model linking a :class:`~apps.devices.models.Device` to a
    :class:`CredentialProfile` for a specific ``purpose`` (one credential per
    purpose per device).
    """

    class Purpose(models.TextChoices):
        SNMP_POLLING = "snmp_polling", "SNMP Polling"
        SSH_CONFIG   = "ssh_config",   "SSH (config push)"
        SSH_BACKUP   = "ssh_backup",   "SSH (config backup)"
        NETCONF      = "netconf",      "NETCONF"
        GNMI         = "gnmi",         "gNMI"
        HTTP_API     = "http_api",     "HTTP API"

    device = models.ForeignKey(
        "devices.Device", on_delete=models.CASCADE, related_name="credential_links"
    )
    credential = models.ForeignKey(
        CredentialProfile, on_delete=models.CASCADE, related_name="device_links"
    )
    purpose = models.CharField(max_length=20, choices=Purpose.choices, db_index=True)
    is_primary = models.BooleanField(default=False)
    last_used = models.DateTimeField(null=True, blank=True)
    last_success = models.DateTimeField(null=True, blank=True)
    failure_count = models.PositiveIntegerField(default=0)
    notes = models.TextField(blank=True)

    class Meta(TimestampedModel.Meta):
        # A device uses at most one credential per purpose.
        unique_together = [("device", "purpose")]
        indexes = [models.Index(fields=["device", "purpose"])]

    def __str__(self):
        return f"{self.device} → {self.credential} ({self.purpose})"
