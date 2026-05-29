"""
Credential profile model.

A single CredentialProfile can carry *multiple* protocols (SSH, SNMPv2c,
SNMPv3, HTTPS/API, NETCONF, gNMI), each toggled on with an ``*_enabled`` flag
and configured with its own non-secret parameters. A device references exactly
one profile (``Device.credential_profile``); that profile covers every protocol
NetPulse needs for the device.

Secret material — passwords, keys, community strings, tokens — is **never**
stored in PostgreSQL. All of a profile's secrets live together in OpenBao at
``vault_path`` (only non-null values are written). See ``apps.credentials.vault``.
"""
from django.contrib.auth import get_user_model
from django.db import models

from apps.core.models import TimestampedModel

# Logical protocol keys ↔ their `*_enabled` flag. Order is display order.
PROTOCOLS = ["ssh", "snmpv2c", "snmpv3", "https", "netconf", "gnmi"]

PROTOCOL_LABELS = {
    "ssh": "SSH",
    "snmpv2c": "SNMPv2c",
    "snmpv3": "SNMPv3",
    "https": "HTTPS/API",
    "netconf": "NETCONF",
    "gnmi": "gNMI",
}


class CredentialProfile(TimestampedModel):
    """A reusable, multi-protocol credential set; secrets held in OpenBao."""

    class SSHAuthMethod(models.TextChoices):
        PASSWORD = "password", "Password"
        KEY = "key", "SSH Key"

    class SNMPSecurityLevel(models.TextChoices):
        NO_AUTH_NO_PRIV = "noAuthNoPriv", "noAuthNoPriv"
        AUTH_NO_PRIV = "authNoPriv", "authNoPriv"
        AUTH_PRIV = "authPriv", "authPriv"

    class HTTPSAuthType(models.TextChoices):
        BASIC = "basic", "Basic"
        TOKEN = "token", "Bearer Token"
        APIKEY = "apikey", "API Key"

    class TestResult(models.TextChoices):
        UNTESTED = "untested", "Untested"
        SUCCESS = "success", "Success"
        PARTIAL = "partial", "Partial"
        FAILURE = "failure", "Failure"

    name = models.CharField(max_length=255, unique=True, db_index=True)
    description = models.TextField(blank=True)
    # OpenBao KV path holding ALL of this profile's secrets in one object.
    vault_path = models.CharField(max_length=512, blank=True)

    # ── SSH ───────────────────────────────────────────────────────────────────
    ssh_enabled = models.BooleanField(default=False)
    ssh_username = models.CharField(max_length=255, blank=True)
    ssh_auth_method = models.CharField(max_length=10, choices=SSHAuthMethod.choices, blank=True)
    ssh_port = models.PositiveIntegerField(default=22)

    # ── SNMPv2c ───────────────────────────────────────────────────────────────
    snmpv2c_enabled = models.BooleanField(default=False)
    snmpv2c_port = models.PositiveIntegerField(default=161)

    # ── SNMPv3 ────────────────────────────────────────────────────────────────
    snmpv3_enabled = models.BooleanField(default=False)
    snmpv3_username = models.CharField(max_length=255, blank=True)
    snmpv3_security_level = models.CharField(max_length=16, choices=SNMPSecurityLevel.choices, blank=True)
    snmpv3_auth_protocol = models.CharField(max_length=16, blank=True)  # SHA, MD5…
    snmpv3_priv_protocol = models.CharField(max_length=16, blank=True)  # AES, DES…
    snmpv3_port = models.PositiveIntegerField(default=161)

    # ── HTTPS / API ───────────────────────────────────────────────────────────
    https_enabled = models.BooleanField(default=False)
    https_auth_type = models.CharField(max_length=10, choices=HTTPSAuthType.choices, blank=True)
    https_username = models.CharField(max_length=255, blank=True)
    https_port = models.PositiveIntegerField(default=443)
    https_verify_tls = models.BooleanField(default=True)

    # ── NETCONF ───────────────────────────────────────────────────────────────
    netconf_enabled = models.BooleanField(default=False)
    netconf_port = models.PositiveIntegerField(default=830)
    netconf_use_ssh_creds = models.BooleanField(default=True)
    netconf_username = models.CharField(max_length=255, blank=True)

    # ── gNMI ──────────────────────────────────────────────────────────────────
    gnmi_enabled = models.BooleanField(default=False)
    gnmi_username = models.CharField(max_length=255, blank=True)
    gnmi_port = models.PositiveIntegerField(default=57400)
    gnmi_tls_enabled = models.BooleanField(default=True)

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
        pass

    def __str__(self):
        return self.name

    def default_vault_path(self) -> str:
        return f"netpulse/credentials/{self.pk}"

    @property
    def enabled_protocols(self) -> list[str]:
        return [p for p in PROTOCOLS if getattr(self, f"{p}_enabled")]

    def port_for(self, protocol: str) -> int:
        return getattr(self, f"{protocol}_port", 0)

    @property
    def device_count(self) -> int:
        return self.devices.count()
