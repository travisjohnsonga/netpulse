"""Editable Jinja2 templates for pushing standardized config to devices.

A ``ConfigPushTemplate`` is rendered against a device (plus admin-supplied
variables and global ``settings``) and the result pushed over SSH. Built-in
templates are seeded (see ``defaults.py``) and may be edited but not deleted.

Security: sensitive default-variable values (names containing pass/key/secret/
token) are NEVER persisted to the database. When OpenBao is configured they are
stored there (``vault_path``); otherwise they must be supplied at push time.
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.db import models

logger = logging.getLogger(__name__)


class ConfigPushTemplate(models.Model):
    """A reusable, editable config-push template."""

    class Category(models.TextChoices):
        SNMP = "snmp", "SNMP"
        SYSLOG = "syslog", "Syslog"
        NTP = "ntp", "NTP"
        DNS = "dns", "DNS"
        AAA = "aaa", "AAA/RADIUS"
        BANNER = "banner", "Banner/MOTD"
        LOGGING = "logging", "Logging"
        OTHER = "other", "Other"

    name = models.CharField(max_length=128, help_text='e.g. "AOS-CX SNMP v3"')
    description = models.TextField(blank=True)
    category = models.CharField(max_length=32, choices=Category.choices, default=Category.OTHER)
    platform = models.CharField(
        max_length=64, blank=True,
        help_text="e.g. aos_cx, ios; leave blank to allow all platforms")
    template_content = models.TextField(
        help_text="Jinja2 template. Available variables: {{ device }}, "
                  "{{ site }}, {{ settings }} plus any you define.")
    # Non-sensitive default variable values only. Sensitive values live in
    # OpenBao (see store_variables) and are never written here.
    variables = models.JSONField(default=dict, blank=True,
                                 help_text="Default variable values for this template")
    enabled = models.BooleanField(default=True)
    # Seeded built-in templates: editable, but not deletable.
    builtin = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="config_push_templates")

    class Meta:
        ordering = ["category", "name"]

    def __str__(self) -> str:
        return f'{self.name} ({self.platform or "all"})'

    @property
    def vault_path(self) -> str:
        """OpenBao path holding this template's sensitive default variables."""
        return f"netpulse/config_templates/{self.pk}"

    def store_variables(self, raw: dict) -> None:
        """Persist default variables: non-sensitive on the row, sensitive in OpenBao.

        Sensitive values are dropped from ``self.variables`` so plaintext secrets
        never reach the database. Call ``save(update_fields=["variables"])`` after.
        """
        from apps.config_templates.render import is_sensitive
        from apps.credentials import vault

        non_sensitive: dict = {}
        sensitive: dict = {}
        for key, value in (raw or {}).items():
            if is_sensitive(key):
                if value not in ("", None):
                    sensitive[key] = value
            else:
                non_sensitive[key] = value
        self.variables = non_sensitive

        if sensitive and vault.vault_enabled() and self.pk:
            try:
                existing = vault.read_secret(self.vault_path) or {}
                existing.update(sensitive)
                vault.write_secret(self.vault_path, existing)
            except Exception:  # best-effort: secrets can still be supplied at push time
                logger.warning("could not store sensitive template vars in OpenBao for %s", self.pk)

    def default_variables(self, include_secrets: bool = False) -> dict:
        """Merged default variables. Secrets are only read from OpenBao on demand."""
        out = dict(self.variables or {})
        if include_secrets and self.pk:
            from apps.credentials import vault
            if vault.vault_enabled():
                try:
                    out.update(vault.read_secret(self.vault_path) or {})
                except Exception:
                    pass
        return out
