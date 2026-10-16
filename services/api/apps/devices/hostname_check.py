"""
Periodic + on-demand hostname verification for managed devices.

A device's ``hostname`` is the key NetPulse correlates SSH/SNMP/syslog by, but
it can drift in the real network (device renamed, DNS updated). We only set it
at discovery/enrichment, so this module re-checks it for active devices:

  1. SNMP sysName (1.3.6.1.2.1.1.5.0) — most reliable.
  2. DNS reverse lookup — fallback when SNMP is unavailable.

On a change we log it, update the device, raise an INFO alert (a standing
informational record — never auto-resolved), and re-apply hostname rules so a
role/site assignment can follow the new name. Every check also stamps
``hostname_verified_at``. Best-effort throughout: failures are logged, never
raised.

Driven by run_scheduler (every HOSTNAME_CHECK_INTERVAL_S, default 24h), the
``POST /api/devices/{id}/check-hostname/`` endpoint, and device enrichment.
"""
from __future__ import annotations

import logging
import socket

logger = logging.getLogger(__name__)

SOURCE = "hostname_check"
HOSTNAME_CHANGE_RULE_NAME = "Device hostname changed"


def _read_secrets(profile) -> dict:
    if not profile or not profile.vault_path:
        return {}
    try:
        from apps.credentials import vault
        return vault.read_secret(profile.vault_path) or {}
    except Exception as exc:  # noqa: BLE001 — best-effort
        logger.debug("hostname check: could not read secrets for profile %s: %s",
                     getattr(profile, "id", "?"), exc)
        return {}


def _snmp_sysname(device) -> str:
    """Read SNMP sysName for ``device``. Returns '' when SNMP isn't usable/fails."""
    profile = device.credential_profile
    if not profile or not (profile.snmpv3_enabled or profile.snmpv2c_enabled):
        return ""
    import asyncio

    from apps.credentials.snmp_auth import build_snmp_auth
    from .enrich import _OID_SYS_NAME, _clean, _snmp_get

    ip = str(device.management_ip or device.ip_address)
    try:
        res = asyncio.run(_snmp_get(ip, [_OID_SYS_NAME], build_snmp_auth(profile, _read_secrets(profile))))
        return _clean(res.get(_OID_SYS_NAME))
    except Exception as exc:  # noqa: BLE001
        logger.debug("hostname check: SNMP sysName failed for %s: %s", ip, exc)
        return ""


def _dns_reverse(ip: str) -> str:
    """Reverse-DNS the IP → hostname (trailing dot stripped). '' on failure."""
    if not ip:
        return ""
    try:
        name = socket.gethostbyaddr(ip)[0]
        return (name or "").rstrip(".")
    except (socket.herror, socket.gaierror, OSError) as exc:
        logger.debug("hostname check: reverse DNS failed for %s: %s", ip, exc)
        return ""


def _hostname_change_rule():
    """Get/create the system AlertRule used for hostname-change events (INFO)."""
    from apps.alerts.models import AlertRule
    rule, _ = AlertRule.objects.get_or_create(
        name=HOSTNAME_CHANGE_RULE_NAME,
        defaults={
            "description": "Informational alert when a managed device's hostname changes on the network.",
            "severity": AlertRule.Severity.INFO,
            "condition": {"rule_type": "hostname_changed"},
            "cooldown_minutes": 0,
            "is_system": True,
        },
    )
    return rule


def _record_change_alert(device, old: str, new: str) -> None:
    from apps.alerts.gating import rule_enabled
    from apps.alerts.models import AlertEvent
    rule = _hostname_change_rule()
    if not rule_enabled(rule):
        return  # operator disabled the built-in → suppress new alerts
    ip = str(device.management_ip or device.ip_address)
    AlertEvent.objects.create(
        rule=rule,
        state=AlertEvent.State.FIRING,
        labels={
            "source": SOURCE, "device": new, "device_id": device.id,
            "severity": "info", "alert_type": "hostname_changed",
        },
        annotations={
            "title": "Device hostname changed",
            "message": f'{ip} hostname changed from "{old}" to "{new}"',
            "severity": "info", "old_hostname": old, "new_hostname": new,
        },
    )


def check_and_update_hostname(device) -> dict:
    """
    Verify ``device``'s hostname via SNMP sysName then DNS reverse. Stamps
    ``hostname_verified_at`` and, on a real change, updates the hostname, records
    an INFO alert and re-applies hostname rules.

    Returns ``{"hostname_changed", "old_hostname", "new_hostname"}``.
    """
    from django.utils import timezone

    old = device.hostname
    result = {"hostname_changed": False, "old_hostname": old, "new_hostname": old}

    ip = str(device.management_ip or device.ip_address)
    new = _snmp_sysname(device) or _dns_reverse(ip)

    device.hostname_verified_at = timezone.now()

    if not new or new == old:
        device.save(update_fields=["hostname_verified_at", "updated_at"])
        return result

    # hostname is unique — never steal another device's name.
    from .models import Device
    if Device.objects.exclude(pk=device.pk).filter(hostname=new).exists():
        logger.warning("hostname check: %r (for %s) already belongs to another device — not updating",
                       new, old)
        device.save(update_fields=["hostname_verified_at", "updated_at"])
        return result

    device.hostname = new
    device.save(update_fields=["hostname", "hostname_verified_at", "updated_at"])
    logger.info("Hostname changed: %s → %s for %s", old, new, ip)
    try:
        _record_change_alert(device, old, new)
    except Exception as exc:  # noqa: BLE001 — never block the update
        logger.warning("hostname check: failed to record change alert for %s: %s", new, exc)
    try:
        from .hostname_rules import apply_hostname_rules
        apply_hostname_rules(device)
    except Exception as exc:  # noqa: BLE001
        logger.warning("hostname check: rule apply failed for %s: %s", new, exc)

    result.update(hostname_changed=True, new_hostname=new)
    return result


def check_all_hostnames() -> dict:
    """Re-check hostnames for every active device (best-effort per device)."""
    from .models import Device

    checked = changed = 0
    qs = Device.objects.filter(status="active").select_related("credential_profile")
    for device in qs.iterator():
        try:
            if check_and_update_hostname(device)["hostname_changed"]:
                changed += 1
            checked += 1
        except Exception as exc:  # noqa: BLE001
            logger.debug("hostname check failed for %s: %s",
                         device.management_ip or device.ip_address, exc)
    logger.info("Hostname check complete: %d device(s) checked, %d changed", checked, changed)
    return {"checked": checked, "changed": changed}
