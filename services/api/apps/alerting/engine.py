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


def get_team_notification_targets(team) -> list[dict]:
    """
    Resolve a team to a flat list of notification targets:
      - the on-call member if a schedule resolves one, else every member,
        each expanded into the channels they opted into:
          {"type": "email", "address", "name"}
          {"type": "slack_dm", "user_id"}        (member's profile slack_user_id)
          {"type": "discord_mention", "user_id"} (member's profile discord_user_id)
      - plus the team's channel webhooks:
          {"type": "slack_webhook", "url"} / {"type": "discord_webhook", "url"}
    Slack/Discord per-user targets are only included when the member opted in
    AND has the corresponding handle set in their profile.
    """
    from apps.core.models import UserPreferences

    on_call = get_on_call_user(team)
    members = list(team.memberships.select_related("user").all())
    if on_call:
        members = [m for m in members if m.user_id == on_call.id]

    prefs = {p.user_id: p for p in
             UserPreferences.objects.filter(user_id__in=[m.user_id for m in members])}

    targets: list[dict] = []
    for tm in members:
        user = tm.user
        p = prefs.get(user.id)
        if tm.notify_email and user.email:
            targets.append({"type": "email", "address": user.email,
                            "name": user.get_full_name() or user.username})
        if tm.notify_slack and p and p.slack_user_id:
            targets.append({"type": "slack_dm", "user_id": p.slack_user_id})
        if tm.notify_discord and p and p.discord_user_id:
            targets.append({"type": "discord_mention", "user_id": p.discord_user_id})

    if team.slack_webhook_url:
        targets.append({"type": "slack_webhook", "url": team.slack_webhook_url})
    if team.discord_webhook_url:
        targets.append({"type": "discord_webhook", "url": team.discord_webhook_url})
    return targets


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
    device_id = labels.get("device_id")

    # Suppress notifications during a maintenance window.
    from .maintenance import is_in_maintenance
    if is_in_maintenance(device_id=device_id, severity=severity, check_type=check_type):
        alert_event.labels = {**labels, "suppressed": True, "suppressed_reason": "maintenance_window"}
        alert_event.save(update_fields=["labels", "updated_at"])
        return {"matched": False, "route": None, "notified": 0, "suppressed": True}

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
        ok, err = channels.send_email(email, f"[spane] {title}", body)
        AlertNotification.objects.create(
            alert_event=alert_event, escalation_step=step, user=user, team=step.notify_team,
            channel="email", status=AlertNotification.Status.SENT if ok else AlertNotification.Status.FAILED,
            sent_at=timezone.now() if ok else None, error="" if ok else (err or "send failed"),
        )
        notified += 1 if ok else 0

    # Team Slack/Discord webhooks (in addition to per-user email).
    team = step.notify_team
    if team and team.discord_webhook_url:
        payload = channels.discord_embed(title, body, severity or "info", fields=[
            {"name": "Severity", "value": (severity or "info").upper(), "inline": True},
            {"name": "Source", "value": source or "unknown", "inline": True},
        ])
        ok, err = channels.send_discord(team.discord_webhook_url, payload)
        AlertNotification.objects.create(
            alert_event=alert_event, escalation_step=step, team=team,
            channel="discord", status=AlertNotification.Status.SENT if ok else AlertNotification.Status.FAILED,
            sent_at=timezone.now() if ok else None, error="" if ok else (err or "send failed"),
        )
        notified += 1 if ok else 0

    return {"matched": True, "route": route.name, "notified": notified}
