"""
Custom social-auth pipeline steps for NetPulse SSO.

These plug into SOCIAL_AUTH_PIPELINE (see settings/base.py):
  - check_allowed_domain  — enforce per-provider email-domain allowlist + signup
  - assign_default_role    — give new SSO users the provider's default role
  - sync_user_profile      — populate name/email from the identity provider
"""
from __future__ import annotations

from social_core.exceptions import AuthForbidden


def _provider_for(backend):
    from apps.sso.models import SSOProvider
    return SSOProvider.objects.filter(provider=backend.name, is_enabled=True).first()


def check_allowed_domain(backend, details, user=None, *args, **kwargs):
    """
    Block sign-in when the email's domain isn't in the provider's allowlist, and
    block first-time signups when the provider has allow_signup disabled. Runs
    after ``social_user`` (so ``user`` is set for returning users, None for new).
    """
    provider = _provider_for(backend)
    if not provider:
        return

    # Signup gate: a brand-new user (no existing association) needs allow_signup.
    if user is None and not provider.allow_signup:
        raise AuthForbidden(backend)

    if provider.allowed_domains:
        email = (details.get("email") or "").lower()
        domain = email.rsplit("@", 1)[-1] if "@" in email else ""
        allowed = {d.lower().lstrip("@") for d in provider.allowed_domains}
        if domain not in allowed:
            raise AuthForbidden(backend)


def assign_default_role(backend, user, *args, **kwargs):
    """Assign the provider's default role to newly created SSO users."""
    if not user or not kwargs.get("is_new"):
        return
    provider = _provider_for(backend)
    role = (provider.default_role if provider else "") or "viewer"
    if getattr(user, "role", None) != role:
        user.role = role
        user.save(update_fields=["role"])


def sync_user_profile(backend, user, details, *args, **kwargs):
    """Backfill first/last name and email from the IdP when missing."""
    if not user:
        return
    changed = []
    fullname = (details.get("fullname") or "").strip()
    if fullname and not (user.first_name or user.last_name):
        first, *rest = fullname.split()
        user.first_name = first
        user.last_name = " ".join(rest)
        changed += ["first_name", "last_name"]
    email = details.get("email")
    if email and not user.email:
        user.email = email
        changed.append("email")
    if changed:
        user.save(update_fields=changed)
