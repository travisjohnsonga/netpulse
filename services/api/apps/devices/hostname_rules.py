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


def _role_obj(role):
    return {"id": role.id, "name": role.name, "color": role.color} if role else None


def _site_obj(site):
    return {"id": site.id, "name": site.name} if site else None


def preview_hostname_rules(force: bool = False) -> dict:
    """
    Dry-run the bulk hostname-rule apply: compute what would change WITHOUT
    saving. Same matching semantics as ``apply_hostname_rules`` (first match per
    type wins, existing role/site preserved unless ``force``).

    Returns ``{would_update, would_skip, summary}`` — see the API docs.
    """
    from .models import Device, HostnameRule

    rules = list(
        HostnameRule.objects.filter(enabled=True)
        .select_related("role", "site")
        .order_by("priority", "name")
    )
    devices = Device.objects.select_related("role", "site").all()

    would_update, would_skip = [], []

    for device in devices:
        hostname = device.hostname or ""
        matched_any = False
        chosen_role = None      # DeviceRole that would be newly applied
        chosen_site = None      # Site that would be newly applied
        role_blocked = False    # a matching rule offered a role but it's set
        site_blocked = False
        role_done = site_done = False

        for rule in rules:
            if not rule.matches(hostname):
                continue
            matched_any = True
            if (rule.rule_type in (HostnameRule.RuleType.ROLE, HostnameRule.RuleType.BOTH)
                    and rule.role_id and not role_done):
                if force or not device.role_id:
                    chosen_role = rule.role
                else:
                    role_blocked = True
                role_done = True
            if (rule.rule_type in (HostnameRule.RuleType.SITE, HostnameRule.RuleType.BOTH)
                    and rule.site_id and not site_done):
                if force or not device.site_id:
                    chosen_site = rule.site
                else:
                    site_blocked = True
                site_done = True
            if role_done and site_done:
                break

        if chosen_role or chosen_site:
            would_update.append({
                "device_id": device.id,
                "hostname": device.hostname,
                "current_role": _role_obj(device.role),
                "new_role": _role_obj(chosen_role),
                "current_site": _site_obj(device.site),
                "new_site": _site_obj(chosen_site),
            })
        else:
            if not matched_any:
                reason = "no matching rules"
            else:
                parts = []
                if role_blocked:
                    parts.append("role already assigned")
                if site_blocked:
                    parts.append("site already assigned")
                reason = " and ".join(parts) if parts else "no applicable assignment"
            would_skip.append({
                "device_id": device.id,
                "hostname": device.hostname,
                "reason": reason,
            })

    return {
        "would_update": would_update,
        "would_skip": would_skip,
        "summary": {
            "total_devices": len(would_update) + len(would_skip),
            "would_update": len(would_update),
            "would_skip": len(would_skip),
        },
    }
