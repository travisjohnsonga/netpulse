"""
Resolve which CredentialProfile a device should use, based on its explicit
profile then its site's SiteCredential rules.

Order:
  1. Device's explicit credential_profile (if set) — always wins.
  2. Site rule matching the device's role (lowest priority number).
  3. Site-wide rule (role=None) (lowest priority number).
  4. None.
"""
from __future__ import annotations


def resolve_credential_for_device(device):
    """Return the best-matching CredentialProfile for ``device`` (or None)."""
    if getattr(device, "credential_profile_id", None):
        return device.credential_profile
    if not getattr(device, "site_id", None):
        return None
    return resolve_credential(device.site_id, getattr(device, "role_id", None))


def resolve_credential(site_id, role_id=None):
    """Resolve the credential profile for a (site, role) pair (or None)."""
    from .models import SiteCredential

    if not site_id:
        return None
    if role_id:
        match = (SiteCredential.objects
                 .filter(site_id=site_id, role_id=role_id)
                 .select_related("credential_profile")
                 .order_by("priority").first())
        if match:
            return match.credential_profile
    match = (SiteCredential.objects
             .filter(site_id=site_id, role__isnull=True)
             .select_related("credential_profile")
             .order_by("priority").first())
    return match.credential_profile if match else None


def apply_site_credential(device, save: bool = True):
    """
    Assign a resolved site credential to ``device`` when it has none. Returns the
    profile applied (or None). Never overrides an existing credential_profile.
    """
    if getattr(device, "credential_profile_id", None):
        return device.credential_profile
    profile = resolve_credential(getattr(device, "site_id", None), getattr(device, "role_id", None))
    if profile:
        device.credential_profile = profile
        if save:
            device.save(update_fields=["credential_profile", "updated_at"])
    return profile
