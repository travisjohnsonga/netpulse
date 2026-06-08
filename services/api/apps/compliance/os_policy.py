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


def get_os_compliance_status(platform: str, os_version: str) -> str:
    """Resolve (platform, os_version) → policy status string.

    Returns one of approved/preferred/deprecated/prohibited, or 'unknown' when
    no policy for the platform matches the version.
    """
    from .models import ApprovedOSVersion

    policies = list(ApprovedOSVersion.objects.filter(platform=platform))
    policies.sort(key=lambda p: _STATUS_PRECEDENCE.index(p.status)
                  if p.status in _STATUS_PRECEDENCE else len(_STATUS_PRECEDENCE))
    for policy in policies:
        if policy.matches(os_version or ""):
            return policy.status
    return "unknown"


def matching_policy(platform: str, os_version: str):
    """The ApprovedOSVersion that decides the status (or None), for UI display."""
    from .models import ApprovedOSVersion

    policies = list(ApprovedOSVersion.objects.filter(platform=platform))
    policies.sort(key=lambda p: _STATUS_PRECEDENCE.index(p.status)
                  if p.status in _STATUS_PRECEDENCE else len(_STATUS_PRECEDENCE))
    for policy in policies:
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

    if not ApprovedOSVersion.objects.exists():
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
