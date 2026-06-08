"""OS-version policy evaluation + fleet inventory refresh.

`get_os_compliance_status` resolves a (platform, version) to a policy status.
`refresh_discovered_platforms` rebuilds the DiscoveredPlatformModel table from
current inventory. `os_compliance_findings` produces the score delta + findings
the compliance engine folds into a device's result.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Evaluation order when a version matches more than one policy. Most-urgent wins
# so a broad "approved" pattern can't mask a specific "prohibited" one. (The
# spec's `order_by('status')` is alphabetical, which would not honour its own
# "preferred first" intent — this explicit precedence does what's meant.)
_STATUS_PRECEDENCE = ["prohibited", "deprecated", "preferred", "approved"]

# Score penalty + finding metadata per resolved status.
_PENALTIES = {
    "prohibited": (30, "OS_PROHIBITED", "high",
                   "OS version {v} is prohibited. Update required."),
    "deprecated": (15, "OS_DEPRECATED", "medium",
                   "OS version {v} is deprecated. Plan upgrade."),
    "unknown":    (5,  "OS_UNKNOWN", "low",
                   "OS version {v} not in approved policy."),
}


def _ranked_policies(platform: str):
    """Real (non-placeholder) policies for a platform, most-urgent first.

    'unknown'-status rows are auto-seeded placeholders awaiting review — they
    are skipped so they neither match nor influence scoring.
    """
    from .models import ApprovedOSVersion

    policies = [
        p for p in ApprovedOSVersion.objects.filter(platform=platform)
        if p.status != ApprovedOSVersion.Status.UNKNOWN
    ]
    policies.sort(key=lambda p: _STATUS_PRECEDENCE.index(p.status)
                  if p.status in _STATUS_PRECEDENCE else len(_STATUS_PRECEDENCE))
    return policies


def get_os_compliance_status(platform: str, os_version: str) -> str:
    """Resolve (platform, os_version) → policy status string.

    Returns one of approved/preferred/deprecated/prohibited, or 'unknown' when
    no real policy for the platform matches the version.
    """
    for policy in _ranked_policies(platform):
        if policy.matches(os_version or ""):
            return policy.status
    return "unknown"


def matching_policy(platform: str, os_version: str):
    """The ApprovedOSVersion that decides the status (or None), for UI display."""
    for policy in _ranked_policies(platform):
        if policy.matches(os_version or ""):
            return policy
    return None


def os_compliance_findings(device) -> tuple[float, list[dict]]:
    """Score delta (<= 0) + findings for a device's OS-version compliance.

    'approved'/'preferred' versions are clean (no penalty, no finding). When no
    OS policy has been defined at all the feature is considered off, so an
    uncovered version is NOT penalised — only once an admin opts in by adding
    policies does an 'unknown' version draw the -5 nudge.
    """
    from .models import ApprovedOSVersion

    # Feature is "active" only once a real (non-placeholder) policy exists. Until
    # then — including a table full of auto-seeded 'unknown' rows — OS scoring is
    # off so config-only compliance is unchanged.
    if not ApprovedOSVersion.objects.exclude(
        status=ApprovedOSVersion.Status.UNKNOWN
    ).exists():
        return 0.0, []
    status = get_os_compliance_status(device.platform, device.os_version or "")
    if status in ("approved", "preferred"):
        return 0.0, []
    penalty, ftype, severity, msg = _PENALTIES[status]
    finding = {
        "type": ftype,
        "severity": severity,
        "message": msg.format(v=device.os_version or "(unknown)"),
        "os_status": status,
    }
    return -float(penalty), [finding]


def refresh_discovered_platforms() -> int:
    """Rebuild DiscoveredPlatformModel from current device inventory.

    Returns the number of distinct combos now tracked. Stale combos (no devices
    left) are pruned so counts stay truthful.
    """
    from django.db.models import Count

    from apps.devices.models import Device
    from .models import DiscoveredPlatformModel

    combos = (
        Device.objects.values("platform", "model", "os_version")
        .annotate(device_count=Count("id"))
        .exclude(platform="")
    )
    seen_keys: set[tuple[str, str, str]] = set()
    for combo in combos:
        platform = combo["platform"] or ""
        model = combo["model"] or ""
        os_version = combo["os_version"] or ""
        seen_keys.add((platform, model, os_version))
        DiscoveredPlatformModel.objects.update_or_create(
            platform=platform, model=model, os_version=os_version,
            defaults={
                "device_count": combo["device_count"],
                "os_status": get_os_compliance_status(platform, os_version),
            },
        )
    # Prune combos no device matches any more.
    for obj in DiscoveredPlatformModel.objects.all():
        if (obj.platform, obj.model, obj.os_version) not in seen_keys:
            obj.delete()
    return len(seen_keys)


def recompute_statuses() -> None:
    """Recompute os_status for all tracked combos (after a policy change)."""
    from .models import DiscoveredPlatformModel

    for obj in DiscoveredPlatformModel.objects.all():
        new = get_os_compliance_status(obj.platform, obj.os_version)
        if new != obj.os_status:
            obj.os_status = new
            obj.save(update_fields=["os_status"])


def os_summary() -> dict:
    """Fleet-wide OS-compliance tallies for the dashboard / summary endpoint."""
    from apps.devices.models import Device

    counts = {s: 0 for s in ("approved", "preferred", "deprecated", "prohibited", "unknown")}
    total = 0
    # Resolve per distinct combo (cheap) then weight by device_count.
    from django.db.models import Count
    combos = (
        Device.objects.values("platform", "os_version")
        .annotate(n=Count("id"))
        .exclude(platform="")
    )
    for combo in combos:
        n = combo["n"]
        total += n
        status = get_os_compliance_status(combo["platform"], combo["os_version"] or "")
        counts[status] = counts.get(status, 0) + n
    counts["total_devices"] = total
    return counts


def seed_os_versions_from_inventory() -> dict:
    """Auto-create placeholder ApprovedOSVersion rows from inventory.

    For each distinct (platform, os_version) in use that has no policy yet,
    create an exact-match entry with status 'unknown' (needs review) so the
    admin only has to set a status rather than type every version. Returns
    {created, already_existed, devices}.
    """
    from django.db.models import Count

    from apps.devices.models import Device
    from .models import ApprovedOSVersion

    combos = (
        Device.objects.exclude(os_version="").exclude(os_version__isnull=True)
        .exclude(platform="")
        .values("platform", "os_version")
        .annotate(device_count=Count("id"))
        .order_by("platform", "os_version")
    )
    created = already = 0
    devices = 0
    for combo in combos:
        devices += combo["device_count"]
        _, was_created = ApprovedOSVersion.objects.get_or_create(
            platform=combo["platform"], version_pattern=combo["os_version"],
            defaults={
                "is_regex": False,
                "status": ApprovedOSVersion.Status.UNKNOWN,
                "notes": f"Auto-discovered from {combo['device_count']} device(s) in inventory",
            },
        )
        if was_created:
            created += 1
        else:
            already += 1
    return {"created": created, "already_existed": already, "devices": devices}


def note_new_os_version(device) -> bool:
    """If `device`'s OS version has no policy entry, seed a placeholder + alert.

    Called from the device post-save signal. Returns True when a new version was
    recorded (so the caller can avoid duplicate work). Raises no errors on the
    save path — best effort.
    """
    from .models import ApprovedOSVersion

    version = (device.os_version or "").strip()
    platform = (device.platform or "").strip()
    if not version or not platform:
        return False
    if ApprovedOSVersion.objects.filter(platform=platform, version_pattern=version).exists():
        return False
    ApprovedOSVersion.objects.create(
        platform=platform, version_pattern=version, is_regex=False,
        status=ApprovedOSVersion.Status.UNKNOWN,
        notes=f"First seen on {device.hostname}",
    )
    _raise_new_os_version_alert(device, platform, version)
    return True


_NEW_OS_VERSION_RULE_NAME = "New OS Version Detected"


def _new_os_version_rule():
    """Get/create the system AlertRule for new-OS-version events (INFO)."""
    from apps.alerts.models import AlertRule

    rule, _ = AlertRule.objects.get_or_create(
        name=_NEW_OS_VERSION_RULE_NAME,
        defaults={
            "description": "Informational alert when a never-before-seen OS version appears in inventory.",
            "severity": AlertRule.Severity.INFO,
            "condition": {"rule_type": "new_os_version_detected"},
            "cooldown_minutes": 0,
            "is_system": True,
        },
    )
    return rule


def _raise_new_os_version_alert(device, platform: str, version: str) -> None:
    """Raise a standing INFO alert that a never-before-seen OS version showed up."""
    from apps.alerts.models import AlertEvent

    AlertEvent.objects.create(
        rule=_new_os_version_rule(),
        state=AlertEvent.State.FIRING,
        labels={
            "source": "os_policy", "device": device.hostname, "device_id": device.id,
            "severity": "info", "alert_type": "new_os_version_detected",
        },
        annotations={
            "title": f"New OS version detected: {platform} {version}",
            "message": (
                f"New OS version {version} ({platform}) seen on {device.hostname}. "
                f"Review and set its approval status in Settings → Compliance → OS Versions."
            ),
            "severity": "info", "platform": platform, "os_version": version,
        },
    )
