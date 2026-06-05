"""
Hostname pattern rules — auto-assign device role and/or site from the hostname.

Rules are evaluated in priority order (lowest number first). The first matching
rule per assignment type (role / site) wins. By default an existing role/site on
the device is left untouched; pass ``force=True`` to overwrite.

Applied at three points:
  * discovery approval (DiscoveredDevice → Device)
  * device enrichment (after the hostname is known)
  * manual/bulk apply endpoints
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def apply_hostname_rules(device, force: bool = False):
    """
    Apply matching hostname rules to ``device``. First match per type wins
    (lowest priority number). Does not overwrite an existing role/site unless
    ``force=True``. Saves the device only if something changed.

    Returns ``(role_assigned, site_assigned)``.
    """
    from .models import HostnameRule

    hostname = device.hostname or ""
    rules = HostnameRule.objects.filter(enabled=True).order_by("priority", "name")

    role_assigned = False
    site_assigned = False

    for rule in rules:
        if not rule.matches(hostname):
            continue

        if (rule.rule_type in (HostnameRule.RuleType.ROLE, HostnameRule.RuleType.BOTH)
                and rule.role_id
                and not role_assigned
                and (force or not device.role_id)):
            device.role_id = rule.role_id
            role_assigned = True

        if (rule.rule_type in (HostnameRule.RuleType.SITE, HostnameRule.RuleType.BOTH)
                and rule.site_id
                and not site_assigned
                and (force or not device.site_id)):
            device.site_id = rule.site_id
            site_assigned = True

        if role_assigned and site_assigned:
            break

    if role_assigned or site_assigned:
        device.save(update_fields=["role", "site", "updated_at"])
        logger.info(
            "Hostname rules applied to %s: role=%s site=%s",
            hostname, role_assigned, site_assigned)

    return role_assigned, site_assigned
