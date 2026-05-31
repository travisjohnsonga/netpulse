"""
Alert-routing engine — Stage 1: route matching + email notification.

Matching is AND across the route's condition fields; an empty condition list
means "match all". Routes are evaluated in ascending priority and the first
active match wins. On-call resolution, timed multi-step escalation and
acknowledgement land in later stages — Stage 1 fires step 1 by email.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def _matches(condition: list, value) -> bool:
    """Empty condition → match all; else value must be in the condition list."""
    if not condition:
        return True
    return value in condition


def route_matches(route, severity=None, source=None, check_type=None, site_id=None) -> bool:
    """Does this AlertRoute match the given alert attributes? (AND logic)"""
    if not _matches(route.match_severity, severity):
        return False
    if not _matches(route.match_source, source):
        return False
    if not _matches(route.match_check_types, check_type):
        return False
    # Site is an M2M; empty → match all, else the alert's site must be included.
    site_ids = list(route.match_sites.values_list("id", flat=True))
    if site_ids and site_id not in site_ids:
        return False
    return True


def find_matching_route(severity=None, source=None, check_type=None, site_id=None):
    """Return the highest-priority active route matching the alert, or None."""
    from .models import AlertRoute

    for route in AlertRoute.objects.filter(is_active=True).prefetch_related("match_sites").order_by("priority", "id"):
        if route_matches(route, severity, source, check_type, site_id):
            return route
    return None


def get_on_call_user(team):
    """
    Current on-call user for a team: an active OnCallShift (start ≤ now ≤ end),
    else a team lead, else any member, else None. Recurrence expansion beyond a
    plain window is a later refinement.
    """
    from django.utils import timezone

    from .models import OnCallShift

    now = timezone.now()
    shift = (OnCallShift.objects
             .filter(schedule__team=team, start_datetime__lte=now, end_datetime__gte=now)
             .select_related("user").order_by("start_datetime").first())
    if shift:
        return shift.user
    lead = team.memberships.filter(role="lead").select_related("user").first()
    if lead:
        return lead.user
    member = team.memberships.select_related("user").first()
    return member.user if member else None


def step_email_recipients(step) -> list[tuple]:
    """
    (user, email) recipients for an escalation step. An explicit notify_user
    wins; for a team, prefer the current on-call user, else every member opted
    in to email.
    """
    out: list[tuple] = []
    if step.notify_user_id:
        email = getattr(step.notify_user, "email", "")
        if email:
            out.append((step.notify_user, email))
    elif step.notify_team_id:
        on_call = get_on_call_user(step.notify_team)
        if on_call and on_call.email:
            return [(on_call, on_call.email)]
        for m in step.notify_team.memberships.filter(notify_email=True).select_related("user"):
            if m.user.email:
                out.append((m.user, m.user.email))
    return out


def process_alert_event(alert_event) -> dict:
    """
    Match an AlertEvent to a route and fire the policy's first step by email.
    Returns a small summary dict. Safe to call from sync code.
    """
    from django.utils import timezone

    from . import channels
    from .models import AlertNotification

    labels = alert_event.labels or {}
    annotations = alert_event.annotations or {}
    severity = annotations.get("severity") or getattr(alert_event.rule, "severity", None)
    source = labels.get("source")
    check_type = labels.get("check_type")
    site_id = labels.get("site_id")

    route = find_matching_route(severity, source, check_type, site_id)
    if route is None:
        return {"matched": False, "route": None, "notified": 0}

    step = route.escalation_policy.steps.order_by("step_number").first()
    if step is None:
        return {"matched": True, "route": route.name, "notified": 0}

    title = annotations.get("title") or labels.get("title") or f"Alert: {alert_event.rule.name}"
    body = annotations.get("message") or annotations.get("description") or title

    notified = 0
    for user, email in step_email_recipients(step):
        ok, err = channels.send_email(email, f"[NetPulse] {title}", body)
        AlertNotification.objects.create(
            alert_event=alert_event, escalation_step=step, user=user, team=step.notify_team,
            channel="email", status=AlertNotification.Status.SENT if ok else AlertNotification.Status.FAILED,
            sent_at=timezone.now() if ok else None, error="" if ok else (err or "send failed"),
        )
        notified += 1 if ok else 0
    return {"matched": True, "route": route.name, "notified": notified}
