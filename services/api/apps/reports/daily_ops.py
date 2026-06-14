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
import re
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


# Network-device authentication-FAILURE phrases as they appear in syslog across
# vendors (Cisco/Arista "Login failed", OpenSSH "Failed password"/"Invalid user",
# firewalls "access denied", RADIUS/TACACS "rejected"). Bare "radius"/"tacacs"
# are deliberately NOT here — they match success lines like "succeeded with
# RADIUS server" too.
_AUTH_FAILURE_PATTERNS = [
    "authentication failed", "authentication failure", "auth failure",
    "failed password", "invalid user", "access denied", "login failed",
    "failed login", "bad password", "login authentication failed",
    "authentication rejected", "%sec_login-4-login_failed",
]
# Success phrases — used only to (a) exclude false positives from the failure set
# and (b) detect a success that FOLLOWS failures (possible successful brute force).
_AUTH_SUCCESS_PATTERNS = [
    "authentication succeeded", "succeeded with radius", "authentication successful",
    "login successful", "login succeeded", "%sec_login-5-login_success",
    "accepted password", "session opened for user",
]

# A username failing on this many distinct devices, or this many times overall,
# is flagged as notable (credential issue / brute force).
_MULTI_DEVICE_FLAG = 3
_BRUTE_FORCE_FLAG = 5

# Best-effort username extraction from heterogeneous vendor syslog.
_USER_RES = [
    re.compile(r"\[user:\s*([^\]]+)\]", re.I),                       # cisco [user: x]
    re.compile(r'user[=:]\s*"?([A-Za-z0-9._\-\\@$]+)"?', re.I),      # user=x / user: "x"
    re.compile(r"(?:invalid user|for user|for)\s+([A-Za-z0-9._\-\\@$]+)", re.I),
    re.compile(r"authentication for ([A-Za-z0-9._\-\\@$]+)", re.I),
]

_NO_SYSLOG_NOTE = (
    "No device authentication failures found in syslog for this period. "
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


def _extract_user(msg: str):
    for rx in _USER_RES:
        m = rx.search(msg or "")
        if m:
            u = m.group(1).strip().strip('".')
            if u and u.lower() not in ("invalid", "unknown", "user"):
                return u
    return None


def _classify_auth(message: str):
    """Return 'success', 'failure', or None for an auth syslog message.
    Success takes precedence so 'succeeded with RADIUS' is never a failure."""
    m = (message or "").lower()
    if any(p in m for p in _AUTH_SUCCESS_PATTERNS):
        return "success"
    if any(p in m for p in _AUTH_FAILURE_PATTERNS):
        return "failure"
    return None


def _hhmm(dt) -> str:
    return dt.strftime("%H:%M") if dt else ""


def _device_security_events(start, end, site_ids=None) -> dict:
    """
    Authentication FAILURES reported BY network devices, mined from the normalized
    syslog in OpenSearch (``netpulse-logs-*``) — distinct from spane's own login
    audit (see :func:`_spane_access_events`).

    The report shows failures only (grouped by user, with brute-force / multi-
    device flags). Successes are surfaced in ONE case: a success that follows
    failures for the same user (a possible successful brute force).

    Degrades gracefully: any OpenSearch error yields an empty result with
    ``available=False`` so the report still renders.
    """
    from apps.logs.views import _device_identifiers, _execute

    musts = [
        {"range": {"@timestamp": {"gte": start.isoformat(), "lte": end.isoformat()}}},
        {"bool": {"should": [{"match_phrase": {"message": p}}
                             for p in (_AUTH_FAILURE_PATTERNS + _AUTH_SUCCESS_PATTERNS)],
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
            "sort": [{"@timestamp": {"order": "desc"}}], "size": 1000}
    empty = {"total_failures": 0, "unique_sources": 0, "device_count": 0,
             "groups": [], "flags": [], "success_after_failures": [],
             "after_hours_failures": 0, "login_failures": []}
    try:
        raw = _execute(body)
    except Exception as exc:  # noqa: BLE001 — store unavailable must not break the report
        logger.debug("device security-events OpenSearch query failed: %s", exc)
        return {**empty, "available": False, "note": _NO_SYSLOG_NOTE}

    failures, successes = [], []
    for hit in raw.get("hits", {}).get("hits", []):
        src = hit.get("_source", {})
        message = src.get("message") or ""
        kind = _classify_auth(message)
        if kind is None:
            continue
        ts = src.get("@timestamp") or src.get("timestamp")
        dt = _parse_ts(ts)
        row = {"time": ts, "dt": dt, "hostname": src.get("hostname"),
               "source_ip": src.get("source_ip"), "username": _extract_user(message),
               "severity": src.get("severity_name"), "message": message[:300],
               "after_hours": _is_after_hours(dt) if dt else False}
        (successes if kind == "success" else failures).append(row)

    # Group failures by username (falling back to source IP when unknown).
    groups: dict = {}
    for f in failures:
        key = f["username"] or f["source_ip"] or "unknown"
        g = groups.setdefault(key, {
            "username": f["username"], "source_ips": set(), "devices": set(),
            "count": 0, "first": None, "last": None})
        g["count"] += 1
        if f["source_ip"]:
            g["source_ips"].add(f["source_ip"])
        if f["hostname"]:
            g["devices"].add(f["hostname"])
        if f["dt"]:
            g["first"] = f["dt"] if not g["first"] or f["dt"] < g["first"] else g["first"]
            g["last"] = f["dt"] if not g["last"] or f["dt"] > g["last"] else g["last"]

    group_list, flags = [], []
    for key, g in sorted(groups.items(), key=lambda kv: -kv[1]["count"]):
        label = g["username"] or (f"source {key}")
        gl = {
            "username": g["username"], "source_ips": sorted(g["source_ips"]),
            "count": g["count"], "device_count": len(g["devices"]),
            "devices": sorted(g["devices"]),
            "time_range": f"{_hhmm(g['first'])}–{_hhmm(g['last'])}" if g["first"] else "",
        }
        group_list.append(gl)
        if gl["device_count"] >= _MULTI_DEVICE_FLAG:
            flags.append(
                f"⚠️ {label} failed on {gl['device_count']} devices ({g['count']} attempts) "
                f"{gl['time_range']} — possible credential issue or password change not "
                f"propagated. Affected: {', '.join(gl['devices'][:5])}"
                + ("…" if gl["device_count"] > 5 else ""))
        elif g["count"] >= _BRUTE_FORCE_FLAG:
            flags.append(
                f"⚠️ {label} {g['count']} failures from {', '.join(gl['source_ips']) or 'unknown'} "
                f"{gl['time_range']} — possible brute force.")

    # Success-after-failures: a success for a user who also failed earlier today.
    fail_users = {}
    for f in failures:
        if f["username"] and f["dt"]:
            fail_users.setdefault(f["username"], []).append(f)
    saf = []
    for s in successes:
        u, sdt = s["username"], s["dt"]
        if not u or not sdt:
            continue
        earlier = [x for x in fail_users.get(u, []) if x["dt"] and x["dt"] <= sdt]
        if earlier:
            saf.append({"username": u, "device": s["hostname"], "time": s["time"],
                        "fail_count": len(earlier), "at": _hhmm(sdt)})

    sources = {f["source_ip"] for f in failures if f["source_ip"]}
    devices = {f["hostname"] for f in failures if f["hostname"]}
    return {
        "total_failures": len(failures),
        "unique_sources": len(sources),
        "device_count": len(devices),
        "groups": group_list,
        "flags": flags,
        "success_after_failures": saf,
        "after_hours_failures": sum(1 for f in failures if f["after_hours"]),
        # Flat list kept for JSON/detail consumers; capped.
        "login_failures": [{k: v for k, v in f.items() if k != "dt"} for f in failures[:100]],
        "available": True,
        "note": "" if failures else _NO_SYSLOG_NOTE,
    }


# spane access events the report cares about: failed logins, admin/config actions.
_ADMIN_ACTION_TYPES = [
    AuditLog.EventType.USER_CREATED, AuditLog.EventType.USER_UPDATED,
    AuditLog.EventType.USER_DELETED, AuditLog.EventType.USER_ROLE_CHANGED,
    AuditLog.EventType.PASSWORD_CHANGED, AuditLog.EventType.PASSWORD_RESET,
    AuditLog.EventType.CREDENTIAL_CREATED, AuditLog.EventType.CREDENTIAL_UPDATED,
    AuditLog.EventType.CREDENTIAL_DELETED, AuditLog.EventType.SETTINGS_CHANGED,
    AuditLog.EventType.API_KEY_CREATED, AuditLog.EventType.API_KEY_DELETED,
    AuditLog.EventType.SSO_CONFIG_CHANGED,
]


def _spane_access_events(start, end) -> dict:
    """
    spane's OWN access audit (from AuditLog). Per the report's focus on risk, this
    shows failed logins, AFTER-HOURS successful logins (worth noting), new login
    source IPs, and admin/config actions — NOT the full list of routine successes.
    """
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

    def _fmt(e):
        return {"time": e["created_at"].isoformat(), "username": e.get("username", ""),
                "source_ip": e.get("ip_address")}

    after_hours = [_fmt(e) for e in successes if _is_after_hours(e["created_at"])]
    admin_actions = [{
        "time": e["created_at"].isoformat(), "username": e.get("username", ""),
        "event_type": e["event_type"], "target": e.get("target_name") or e.get("target_type", ""),
        "description": (e.get("description") or "")[:200],
    } for e in base.filter(event_type__in=_ADMIN_ACTION_TYPES)
        .values("created_at", "username", "event_type", "target_name", "target_type", "description")]

    return {
        "login_failures": [_fmt(e) for e in failures],
        "after_hours_logins": after_hours,
        "new_source_ips": new_ips,
        "admin_actions": admin_actions,
        "total_failures": len(failures),
        "total_logins": len(successes),
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


# A device is "failing" compliance below this 0-100 score (matches the
# Compliance Summary's PASS_MIN). A trend delta beyond ±this is "significant".
_COMPLIANCE_FAIL_BELOW = 70
_TREND_DELTA = 5.0


def _day_scores(day_start, day_end, dev_ids=None) -> dict:
    """
    device_id -> averaged compliance score for the day, from the latest stored
    ComplianceTemplateResult per (device, template) within the window. Reads the
    persisted template-compliance history only — no live device calls — so it is
    cheap enough for the daily report.
    """
    from apps.compliance.models import ComplianceTemplateResult as CTR
    qs = CTR.objects.filter(checked_at__gte=day_start, checked_at__lte=day_end,
                            score__isnull=False)
    if dev_ids is not None:
        qs = qs.filter(device_id__in=dev_ids)
    seen, per_dev = set(), {}
    for did, tid, score in (qs.order_by("device_id", "template_id", "-checked_at")
                            .values_list("device_id", "template_id", "score")):
        key = (did, tid)
        if key in seen:  # keep only the latest result per template
            continue
        seen.add(key)
        per_dev.setdefault(did, []).append(score)
    return {did: round(sum(v) / len(v), 1) for did, v in per_dev.items()}


def _compliance_events(start, end, site_ids=None) -> dict:
    from apps.alerts.models import AlertEvent
    from apps.compliance.device_score import score_to_grade
    from apps.devices.models import Device

    dev_ids = None
    if site_ids:
        dev_ids = list(Device.objects.filter(site_id__in=site_ids).values_list("id", flat=True))

    # Current fleet state + day-over-day trend from stored template scores.
    today = _day_scores(start, end, dev_ids)
    prev_start, prev_end = start - timedelta(days=1), end - timedelta(days=1)
    prev = _day_scores(prev_start, prev_end, dev_ids)

    devmap = {d.id: d for d in Device.objects.select_related("site")
              .filter(id__in=set(today) | set(prev))}

    def _avg(scores):
        vals = list(scores)
        return round(sum(vals) / len(vals), 1) if vals else None

    fleet_today, fleet_prev = _avg(today.values()), _avg(prev.values())
    fleet_delta = (round(fleet_today - fleet_prev, 1)
                   if fleet_today is not None and fleet_prev is not None else None)

    failing = sorted(
        ({"hostname": devmap[d].hostname if d in devmap else str(d),
          "score": s, "grade": score_to_grade(s),
          "site": (devmap[d].site.name if d in devmap and devmap[d].site else None)}
         for d, s in today.items() if s < _COMPLIANCE_FAIL_BELOW),
        key=lambda r: r["score"])

    degraded, improved = [], []
    for d, s in today.items():
        p = prev.get(d)
        if p is None:
            continue
        delta = round(s - p, 1)
        row = {"hostname": devmap[d].hostname if d in devmap else str(d),
               "score_today": s, "score_prev": p, "delta": delta}
        if delta <= -_TREND_DELTA:
            degraded.append(row)
        elif delta >= _TREND_DELTA:
            improved.append(row)
    degraded.sort(key=lambda r: r["delta"])
    improved.sort(key=lambda r: -r["delta"])

    # Standing unsaved-config signal (daily new/resolved alerts + current total).
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

    return {
        "fleet_avg_today": fleet_today,
        "fleet_avg_prev": fleet_prev,
        "fleet_avg_delta": fleet_delta,
        "fleet_grade": score_to_grade(fleet_today) if fleet_today is not None else None,
        "total_failing_devices": len(failing),
        "failing_devices": failing[:20],
        "degraded": degraded,
        "improved": improved,
        "unsaved_configs": len(unsaved_config_devices()),
        "new_failures": new_failures,
        "resolved": resolved,
    }


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

    # Per-status breakdown (success/unchanged/timeout/auth_failed/failed/empty).
    by_status = Counter()
    devices = set()
    failed_rows = Counter()
    errors = {}
    for r in qs.values("device__hostname", "device_id", "status"):
        by_status[r["status"]] += 1
        devices.add(r["device_id"])
        if r["status"] not in reached:
            failed_rows[r["device__hostname"]] += 1
            errors[r["device__hostname"]] = r["status"]

    status_breakdown = [
        {"status": s, "count": c, "rate": round(c / total * 100, 1) if total else 0.0}
        for s, c in by_status.most_common()
    ]
    return {
        "total_attempts": total, "successful": successful, "failed": total - successful,
        "device_count": len(devices),
        "success_rate": round(successful / total * 100, 1) if total else 0.0,
        "by_status": status_breakdown,
        "failed_devices": [{"hostname": h, "error": errors[h], "attempts": n}
                           for h, n in failed_rows.most_common()],
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
        "%d spane login failures, %d collection-log attempts",
        day, availability["total_outages"], len(config_changes),
        security["total_failures"], spane_access["total_failures"],
        collection["total_attempts"],
    )
    return {
        "report_date": day.isoformat(),
        "generated_at": timezone.now().isoformat(),
        "period": {"start": start.isoformat(), "end": end.isoformat()},
        # Section 1: security events reported BY network devices (syslog).
        "security_events": security,
        "device_availability": availability,
        "compliance_events": _compliance_events(start, end, site_ids),
        "config_changes": config_changes,
        "collection_health": collection,
        "agent_health": _agent_health(),
        "alerts_summary": _alerts_summary(start, end),
        # Section 7: spane's own access audit (who logged into spane).
        "spane_access_events": spane_access,
    }
