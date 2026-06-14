"""
Daily Operations report data builder.

Aggregates a single day's operational signal across security (logins), device
availability, compliance events, config changes, collection health, agent
health and alerts.

Device downtime is reconstructed from the ``device-unreachable`` AlertEvent
history (the reachability monitor opens a FIRING event when a device goes down
and the recovery path flips that same event to RESOLVED, stamping resolved_at
with the recovery time). This captures outages that started AND recovered within
the day — which ``Device.unreachable_since`` alone cannot, since it is reset to
NULL on recovery.
"""
from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime, time, timedelta

from django.utils import timezone

from apps.core.models import AuditLog

logger = logging.getLogger(__name__)

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


# Network-device authentication-failure phrases as they appear in syslog across
# vendors (Cisco/Arista "Login failed"/"authentication failure", Linux/OpenSSH
# "Failed password"/"Invalid user", firewalls "access denied", etc.).
_AUTH_FAILURE_PATTERNS = [
    "authentication failure", "authentication failed", "failed password",
    "invalid user", "access denied", "login failed", "failed login",
    "auth failure", "bad password", "login authentication failed",
    "%sec_login-4-login_failed", "tacacs", "radius",
]

_NO_SYSLOG_NOTE = (
    "No device authentication events found in syslog for this period. "
    "Forward TACACS+/RADIUS and device auth syslog to spane (UDP/TCP 514) for "
    "full device authentication tracking."
)


def _parse_ts(value):
    """Parse an ISO/epoch timestamp string from a log doc into an aware datetime."""
    if not value:
        return None
    from django.utils.dateparse import parse_datetime
    try:
        dt = parse_datetime(str(value))
    except (TypeError, ValueError):
        dt = None
    if dt is not None and timezone.is_naive(dt):
        dt = timezone.make_aware(dt, timezone.utc)
    return dt


def _device_security_events(start, end, site_ids=None) -> dict:
    """
    Authentication / security events reported BY network devices, mined from the
    normalized syslog in OpenSearch (``netpulse-logs-*``) — distinct from spane's
    own login audit (see :func:`_spane_access_events`).

    Degrades gracefully: any OpenSearch error (store down, no index) yields an
    empty result with ``available=False`` so the report still renders.
    """
    from apps.logs.views import _device_identifiers, _execute

    musts = [
        {"range": {"@timestamp": {"gte": start.isoformat(), "lte": end.isoformat()}}},
        {"bool": {"should": [{"match_phrase": {"message": p}} for p in _AUTH_FAILURE_PATTERNS],
                  "minimum_should_match": 1}},
    ]
    if site_ids:
        from apps.devices.models import Device
        ids = _device_identifiers(Device.objects.filter(site_id__in=site_ids))
        if ids:
            musts.append({"bool": {"should": [
                {"terms": {"hostname.keyword": ids}},
                {"terms": {"source_ip.keyword": ids}}], "minimum_should_match": 1}})

    body = {"query": {"bool": {"must": musts}},
            "sort": [{"@timestamp": {"order": "desc"}}], "size": 500}
    try:
        raw = _execute(body)
    except Exception as exc:  # noqa: BLE001 — store unavailable must not break the report
        logger.debug("device security-events OpenSearch query failed: %s", exc)
        return {"login_failures": [], "total_failures": 0, "unique_sources": 0,
                "after_hours_failures": 0, "available": False, "note": _NO_SYSLOG_NOTE}

    failures = []
    for hit in raw.get("hits", {}).get("hits", []):
        src = hit.get("_source", {})
        ts = src.get("@timestamp") or src.get("timestamp")
        dt = _parse_ts(ts)
        failures.append({
            "time": ts,
            "hostname": src.get("hostname"),
            "source_ip": src.get("source_ip"),
            "severity": src.get("severity_name"),
            "message": (src.get("message") or "")[:300],
            "after_hours": _is_after_hours(dt) if dt else False,
        })
    sources = {f["source_ip"] for f in failures if f["source_ip"]}
    return {
        "login_failures": failures,
        "total_failures": len(failures),
        "unique_sources": len(sources),
        "after_hours_failures": sum(1 for f in failures if f["after_hours"]),
        "available": True,
        "note": "" if failures else _NO_SYSLOG_NOTE,
    }


def _spane_access_events(start, end) -> dict:
    """spane's OWN access audit (who logged into spane), from AuditLog."""
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
    """
    Reconstruct the day's outages from device-unreachable AlertEvents.

    The reachability monitor (via the stream-processor) opens a FIRING event
    ``rule="device-unreachable"``, ``labels.source="reachability_monitor"``,
    ``annotations.title="Device X unreachable"`` when a device goes down. On
    recovery the SAME event is flipped to RESOLVED with ``resolved_at`` set to
    the recovery time (a separate FIRING "reachable again" event is also emitted
    — we skip those). So: created_at = down time, resolved_at = recovery time,
    still-FIRING = still down.
    """
    from apps.alerts.models import AlertEvent
    from apps.devices.models import Device
    now = timezone.now()

    dev_qs = Device.objects.select_related("site", "role")
    if site_ids:
        dev_qs = dev_qs.filter(site_id__in=site_ids)
    # device_id -> Device for site/role labelling (and site scoping below).
    dev_meta = {d.id: d for d in dev_qs}

    # Outages that *started* in the window. Filter to the device-unreachable rule
    # + reachability_monitor source so latency alerts (same source, different
    # rule) are excluded; the title check then drops the paired "reachable again"
    # recovery events (done in Python — JSON icontains is unreliable on SQLite).
    down_events = (AlertEvent.objects
                   .filter(rule__name="device-unreachable",
                           labels__source="reachability_monitor",
                           created_at__gte=start, created_at__lte=end)
                   .order_by("created_at"))

    went_down, still_down, total_downtime = [], [], 0
    for e in down_events:
        title = (e.annotations or {}).get("title", "")
        if "unreachable" not in title.lower():  # excludes "reachable again"
            continue
        did = e.labels.get("device_id")
        try:
            did = int(did) if did is not None else None
        except (TypeError, ValueError):
            did = None
        if site_ids and did not in dev_meta:
            continue  # outside the requested sites
        dev = dev_meta.get(did)
        recovered = e.state == AlertEvent.State.RESOLVED and e.resolved_at is not None
        recovered_at = e.resolved_at if recovered else None
        end_t = recovered_at or now
        minutes = max(0, int((end_t - e.created_at).total_seconds() // 60))
        total_downtime += minutes
        row = {
            "hostname": e.labels.get("hostname") or (dev.hostname if dev else ""),
            "down_at": e.created_at.isoformat(),
            "recovered_at": recovered_at.isoformat() if recovered_at else None,
            "duration_minutes": minutes,
            "site": dev.site.name if dev and dev.site else None,
            "role": dev.role.name if dev and dev.role else None,
            "still_down": not recovered,
        }
        went_down.append(row)
        if not recovered:
            still_down.append(row)

    monitored = dev_qs.filter(status__in=[Device.Status.ACTIVE, Device.Status.UNREACHABLE]).count()
    reachable = dev_qs.filter(is_reachable=True).count()
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
    security = _device_security_events(start, end, site_ids)
    spane_access = _spane_access_events(start, end)
    availability = _device_availability(start, end, site_ids)
    config_changes = _config_changes(start, end, site_ids)
    collection = _collection_health(start, end, site_ids)
    logger.info(
        "Daily ops %s: %d outages, %d config changes, %d device auth failures, "
        "%d spane logins, %d collection-log attempts",
        day, availability["total_outages"], len(config_changes),
        security["total_failures"], len(spane_access["successful_logins"]),
        collection["total_attempts"],
    )
    return {
        "report_date": day.isoformat(),
        "generated_at": timezone.now().isoformat(),
        "period": {"start": start.isoformat(), "end": end.isoformat()},
        # Section 1: security events reported BY network devices (syslog).
        "security_events": security,
        "device_availability": availability,
        "compliance_events": _compliance_events(start, end),
        "config_changes": config_changes,
        "collection_health": collection,
        "agent_health": _agent_health(),
        "alerts_summary": _alerts_summary(start, end),
        # Section 7: spane's own access audit (who logged into spane).
        "spane_access_events": spane_access,
    }
