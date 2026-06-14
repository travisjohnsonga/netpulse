"""
Daily Operations report data builder.

Aggregates a single day's operational signal across security (logins), device
availability, compliance events, config changes, collection health, agent
health and alerts.

Note: spane does not yet persist a discrete outage-history table, so device
downtime is derived from ``Device.unreachable_since`` — accurate for the start
of an outage and for still-down devices; recovery times within the day are
approximate.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, time, timedelta

from django.utils import timezone

from apps.core.models import AuditLog

AFTER_HOURS_START = 20  # 20:00 UTC
AFTER_HOURS_END = 6     # 06:00 UTC


def _day_bounds(date_str=None):
    """Return (start, end, date) for the given YYYY-MM-DD (default: yesterday, UTC)."""
    if date_str:
        d = datetime.strptime(date_str, "%Y-%m-%d").date()
    else:
        d = (timezone.now() - timedelta(days=1)).date()
    start = timezone.make_aware(datetime.combine(d, time.min))
    end = timezone.make_aware(datetime.combine(d, time.max))
    return start, end, d


def _is_after_hours(dt) -> bool:
    # Timestamps are stored in UTC; compare the UTC hour for determinism.
    return dt.hour >= AFTER_HOURS_START or dt.hour < AFTER_HOURS_END


def _security_events(start, end) -> dict:
    base = AuditLog.objects.filter(created_at__gte=start, created_at__lte=end)
    failures = list(base.filter(event_type=AuditLog.EventType.LOGIN_FAILED)
                    .values("created_at", "username", "ip_address", "error_message"))
    successes = list(base.filter(event_type=AuditLog.EventType.LOGIN_SUCCESS)
                     .values("created_at", "username", "ip_address"))

    # New source IPs: login IPs in the window never seen in login events before it.
    prior_ips = set(AuditLog.objects.filter(
        created_at__lt=start,
        event_type__in=[AuditLog.EventType.LOGIN_SUCCESS, AuditLog.EventType.LOGIN_FAILED],
    ).exclude(ip_address__isnull=True).values_list("ip_address", flat=True))
    window_ips = {e["ip_address"] for e in (failures + successes) if e["ip_address"]}
    new_ips = sorted(window_ips - prior_ips)

    def _fmt(e, succeeded=None):
        row = {"time": e["created_at"].isoformat(), "username": e.get("username", ""),
               "source_ip": e.get("ip_address")}
        if succeeded is not None:
            row["succeeded"] = succeeded
        return row

    after_hours = [_fmt(e, True) for e in successes if _is_after_hours(e["created_at"])]
    return {
        "login_failures": [_fmt(e, False) for e in failures],
        "successful_logins": [_fmt(e, True) for e in successes],
        "after_hours_logins": after_hours,
        "new_source_ips": new_ips,
        "total_failures": len(failures),
        "unique_sources": len({e["ip_address"] for e in failures if e["ip_address"]}),
    }


def _device_availability(start, end, site_ids) -> dict:
    from apps.devices.models import Device
    now = timezone.now()
    qs = Device.objects.select_related("site", "role")
    if site_ids:
        qs = qs.filter(site_id__in=site_ids)

    went_down = []
    still_down = []
    total_downtime = 0
    for d in qs.filter(unreachable_since__gte=start, unreachable_since__lte=end):
        down_at = d.unreachable_since
        recovered = d.is_reachable  # recovered if reachable again
        end_t = (now if not recovered else d.last_reachability_check or now)
        minutes = max(0, int((end_t - down_at).total_seconds() // 60)) if down_at else 0
        total_downtime += minutes
        row = {
            "hostname": d.hostname, "down_at": down_at.isoformat() if down_at else None,
            "recovered_at": None if not recovered else (d.last_reachability_check.isoformat()
                                                        if d.last_reachability_check else None),
            "duration_minutes": minutes,
            "site": d.site.name if d.site else None,
            "role": d.role.name if d.role else None,
        }
        went_down.append(row)
        if not recovered:
            still_down.append(row)

    monitored = qs.filter(status__in=[Device.Status.ACTIVE, Device.Status.UNREACHABLE]).count()
    reachable = qs.filter(is_reachable=True).count()
    availability = round(reachable / monitored * 100, 1) if monitored else 100.0
    return {
        "went_down": went_down, "still_down": still_down,
        "total_outages": len(went_down), "total_downtime_minutes": total_downtime,
        "availability_pct": availability,
    }


def _compliance_events(start, end) -> dict:
    from apps.alerts.models import AlertEvent
    from apps.configbackup.stats import unsaved_config_devices

    new_failures = [{
        "hostname": e.labels.get("device", ""),
        "check": e.labels.get("alert_type", "compliance"),
        "detected_at": e.created_at.isoformat(),
        "severity": e.labels.get("severity", "warning"),
    } for e in AlertEvent.objects.filter(
        created_at__gte=start, created_at__lte=end,
        labels__alert_type__in=["config_unsaved"]).select_related("rule")]

    resolved = [{
        "hostname": e.labels.get("device", ""),
        "check": e.labels.get("alert_type", "compliance"),
        "resolved_at": e.resolved_at.isoformat() if e.resolved_at else None,
    } for e in AlertEvent.objects.filter(
        state=AlertEvent.State.RESOLVED, resolved_at__gte=start, resolved_at__lte=end,
        labels__alert_type__in=["config_unsaved"])]

    return {"new_failures": new_failures, "resolved": resolved,
            "total_failing_devices": len(unsaved_config_devices())}


# Cap the per-change diff so a huge config churn can't blow up the report.
_MAX_DIFF_LINES = 600


def _short_summary(diff_lines) -> str:
    """A concise human description from a unified diff (top-level stanzas touched)."""
    added, removed = [], []
    for ln in diff_lines:
        if ln.startswith("+") and not ln.startswith("+++"):
            body = ln[1:]
            if body and not body[0].isspace() and body.strip():
                added.append(body.strip())
        elif ln.startswith("-") and not ln.startswith("---"):
            body = ln[1:]
            if body and not body[0].isspace() and body.strip():
                removed.append(body.strip())
    parts = []
    if added:
        parts.append("added: " + ", ".join(list(dict.fromkeys(added))[:3]))
    if removed:
        parts.append("removed: " + ", ".join(list(dict.fromkeys(removed))[:3]))
    return ("; ".join(parts))[:200]


def _full_diff(prev_content, cur_content, prev_at, cur_at) -> str:
    """Unified diff between two config snapshots (capped)."""
    import difflib
    lines = list(difflib.unified_diff(
        (prev_content or "").splitlines(), (cur_content or "").splitlines(),
        fromfile=f"config at {prev_at}", tofile=f"config at {cur_at}", lineterm=""))
    if len(lines) > _MAX_DIFF_LINES:
        lines = lines[:_MAX_DIFF_LINES] + [f"… (diff truncated at {_MAX_DIFF_LINES} lines)"]
    return "\n".join(lines)


def _config_changes(start, end, site_ids) -> list:
    from apps.configbackup.models import DeviceConfig
    qs = (DeviceConfig.objects
          .select_related("device", "device__site", "device__role")
          .filter(changed_from_previous=True, collected_at__gte=start, collected_at__lte=end)
          .order_by("collected_at"))
    if site_ids:
        qs = qs.filter(device__site_id__in=site_ids)
    out = []
    for c in qs:
        dev = c.device
        prev = (DeviceConfig.objects
                .filter(device=dev, config_type=c.config_type, collected_at__lt=c.collected_at)
                .order_by("-collected_at").first())
        # Prefer an on-the-fly diff of the two snapshots; fall back to the stored
        # (normalized) diff_summary when there's no prior snapshot.
        if prev is not None:
            diff = _full_diff(prev.content, c.content, prev.collected_at.isoformat(),
                              c.collected_at.isoformat())
        else:
            diff = c.diff_summary or ""
        diff_lines = diff.splitlines()
        added = sum(1 for ln in diff_lines if ln.startswith("+") and not ln.startswith("+++"))
        removed = sum(1 for ln in diff_lines if ln.startswith("-") and not ln.startswith("---"))
        out.append({
            "hostname": dev.hostname,
            "site": dev.site.name if dev.site else None,
            "role": dev.role.name if dev.role else None,
            "platform": dev.platform or None,
            "detected_at": c.collected_at.isoformat(),
            "collected_by": c.collected_by,
            "lines_added": added, "lines_removed": removed,
            "diff_summary": _short_summary(diff_lines),
            "diff": diff,
            "previous_backup_at": prev.collected_at.isoformat() if prev else None,
            "current_backup_at": c.collected_at.isoformat(),
        })
    return out


def _collection_health(start, end, site_ids) -> dict:
    from apps.configbackup.models import ConfigCollectionLog
    qs = ConfigCollectionLog.objects.select_related("device").filter(
        collected_at__gte=start, collected_at__lte=end)
    if site_ids:
        qs = qs.filter(device__site_id__in=site_ids)
    reached = set(ConfigCollectionLog.REACHED_STATUSES)
    total = qs.count()
    successful = qs.filter(status__in=reached).count()
    failed_rows = Counter()
    errors = {}
    for r in qs.exclude(status__in=reached).values("device__hostname", "status"):
        failed_rows[r["device__hostname"]] += 1
        errors[r["device__hostname"]] = r["status"]
    return {
        "total_attempts": total, "successful": successful, "failed": total - successful,
        "failed_devices": [{"hostname": h, "error": errors[h], "attempts": n}
                           for h, n in failed_rows.items()],
    }


def _agent_health() -> dict:
    from apps.agents.models import Agent
    now = timezone.now()
    active = Agent.objects.filter(status=Agent.Status.ACTIVE)
    online, offline, issues = 0, 0, []
    for a in active:
        grace = (a.collection_interval or 30) * 4
        if a.last_seen and (now - a.last_seen).total_seconds() <= grace:
            online += 1
        else:
            offline += 1
            issues.append({"hostname": a.hostname,
                           "last_seen": a.last_seen.isoformat() if a.last_seen else None})
    return {"total_agents": active.count(), "online": online, "offline": offline,
            "last_seen_issues": issues}


def _alerts_summary(start, end) -> dict:
    from apps.alerts.models import AlertEvent
    qs = AlertEvent.objects.filter(created_at__gte=start, created_at__lte=end).select_related("rule")
    by_sev = Counter()
    by_type = Counter()
    for e in qs:
        by_sev[(e.rule.severity if e.rule else "info")] += 1
        by_type[e.labels.get("alert_type", "other")] += 1
    total = sum(by_sev.values())
    return {
        "total": total,
        "critical": by_sev.get("critical", 0), "high": by_sev.get("high", 0),
        "medium": by_sev.get("medium", 0), "low": by_sev.get("low", 0) + by_sev.get("info", 0),
        "by_type": dict(by_type),
    }


def build_daily_ops(*, date=None, site_ids=None) -> dict:
    start, end, day = _day_bounds(date)
    return {
        "report_date": day.isoformat(),
        "generated_at": timezone.now().isoformat(),
        "period": {"start": start.isoformat(), "end": end.isoformat()},
        "security_events": _security_events(start, end),
        "device_availability": _device_availability(start, end, site_ids),
        "compliance_events": _compliance_events(start, end),
        "config_changes": _config_changes(start, end, site_ids),
        "collection_health": _collection_health(start, end, site_ids),
        "agent_health": _agent_health(),
        "alerts_summary": _alerts_summary(start, end),
    }
