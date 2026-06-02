from django.db import models


class SSOProvider(models.Model):
    """
    Configuration for an external SSO identity provider.

    The ``client_secret`` is NEVER stored here — it lives in OpenBao at
    ``secret/sso/{id}/credentials`` (referenced by ``vault_path``). This model
    holds only non-sensitive configuration. ``allowed_domains`` is a JSONField
    (not Postgres ArrayField) so the SQLite test database works.
    """

    class Provider(models.TextChoices):
        GOOGLE = "google-oauth2", "Google Workspace"
        AZURE = "azuread-tenant-oauth2", "Microsoft Azure AD"
        OKTA = "okta-oauth2", "Okta"
        GITHUB = "github", "GitHub"
        SAML = "saml", "SAML 2.0"
        LDAP = "ldap", "LDAP / Active Directory"

    name = models.CharField(max_length=120)
    provider = models.CharField(max_length=40, choices=Provider.choices)

    client_id = models.CharField(max_length=255, blank=True)
    # client_secret is stored in OpenBao; this references that secret.
    vault_path = models.CharField(max_length=255, blank=True)

    # Provider-specific.
    tenant_id = models.CharField(max_length=255, blank=True)         # Azure AD
    okta_domain = models.CharField(max_length=255, blank=True)       # company.okta.com
    saml_metadata_url = models.URLField(blank=True)

    # Behaviour.
    is_enabled = models.BooleanField(default=True)
    is_default = models.BooleanField(default=False)                  # auto-redirect
    allow_signup = models.BooleanField(default=True)                 # create users on first login
    default_role = models.CharField(max_length=10, default="viewer")  # role for new SSO users
    allowed_domains = models.JSONField(default=list, blank=True)     # ["company.com"]; [] = any

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.provider})"

    def default_vault_path(self) -> str:
        return f"secret/sso/{self.pk}/credentials"

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        # Only one provider may be the auto-redirect default.
        if self.is_default:
            SSOProvider.objects.exclude(pk=self.pk).filter(is_default=True).update(is_default=False)
