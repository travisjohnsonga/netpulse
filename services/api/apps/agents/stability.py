"""Service-stability tracking + alerting (role-INDEPENDENT).

The agent reports rich ServiceStat for the operator's watched services
(``desired_config.stability.services``) on every check-in. This records each
watched service's state + transitions in ``WatchedServiceStatus`` and fires/
resolves **"Service Down"** and **"Service Flapping"** alerts through the existing
``AlertEvent`` plumbing (debounce + auto-resolve, exactly like agent-offline).
No role required — you watch the services you care about.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from django.utils import timezone

from .models import (
    STABILITY_FLAP_THRESHOLD, STABILITY_FLAP_WINDOW_S, WatchedServiceStatus,
)

logger = logging.getLogger(__name__)

_DOWN_RULE = "Service Down"
_FLAP_RULE = "Service Flapping"


def _rule(name, severity, description, rtype):
    from apps.alerts.models import AlertRule
    rule, _ = AlertRule.objects.get_or_create(
        name=name,
        defaults={"description": description, "severity": severity,
                  "condition": {"rule_type": rtype}, "cooldown_minutes": 0,
                  "is_system": True},
    )
    return rule


def _open_event(agent, name, alert_type):
    from apps.alerts.models import AlertEvent
    return AlertEvent.objects.filter(
        state=AlertEvent.State.FIRING, labels__alert_type=alert_type,
        labels__agent_id=str(agent.id), labels__service=name).first()


def _resolve(ev, now, note):
    from apps.alerts.models import AlertEvent
    ev.state = AlertEvent.State.RESOLVED
    ev.resolved_at = now
    ev.resolution_note = note
    ev.save(update_fields=["state", "resolved_at", "resolution_note"])


def _trim(restarts, now, seconds):
    """Return restart ISO timestamps within the last `seconds`."""
    cutoff = now - timedelta(seconds=seconds)
    out = []
    for t in restarts or []:
        try:
            if datetime.fromisoformat(t) >= cutoff:
                out.append(t)
        except (ValueError, TypeError):
            continue
    return out


def _reconcile_down(agent, ws, now):
    from apps.alerts.models import AlertEvent, AlertRule
    open_ev = _open_event(agent, ws.name, "service_down")
    if not ws.running:
        if open_ev is None:  # debounce: only on the transition to down
            AlertEvent.objects.create(
                rule=_rule(_DOWN_RULE, AlertRule.Severity.HIGH,
                           "A watched service stopped running.", "service_down"),
                state=AlertEvent.State.FIRING,
                labels={"source": "service_stability", "alert_type": "service_down",
                        "agent_id": str(agent.id), "service": ws.name,
                        "hostname": agent.hostname, "severity": "critical"},
                annotations={
                    "title": f"Service down: {ws.name} on {agent.hostname}",
                    "message": (f"Watched service '{ws.name}' is "
                                f"{ws.state or 'stopped'} on {agent.hostname}."),
                    "severity": "critical"},
            )
            return (1, 0)
        return (0, 0)
    if open_ev is not None:
        _resolve(open_ev, now, "Service is running again.")
        return (0, 1)
    return (0, 0)


def _reconcile_flap(agent, ws, now):
    from apps.alerts.models import AlertEvent, AlertRule
    recent = _trim(ws.restarts, now, STABILITY_FLAP_WINDOW_S)
    open_ev = _open_event(agent, ws.name, "service_flapping")
    if len(recent) >= STABILITY_FLAP_THRESHOLD:
        if open_ev is None:
            AlertEvent.objects.create(
                rule=_rule(_FLAP_RULE, AlertRule.Severity.MEDIUM,
                           "A watched service is restarting repeatedly.", "service_flapping"),
                state=AlertEvent.State.FIRING,
                labels={"source": "service_stability", "alert_type": "service_flapping",
                        "agent_id": str(agent.id), "service": ws.name,
                        "hostname": agent.hostname, "severity": "warning"},
                annotations={
                    "title": f"Service flapping: {ws.name} on {agent.hostname}",
                    "message": (f"'{ws.name}' restarted {len(recent)} times in the last "
                                f"{STABILITY_FLAP_WINDOW_S // 60} min on {agent.hostname}."),
                    "severity": "warning"},
            )
            return (1, 0)
        return (0, 0)
    if not recent and open_ev is not None:
        _resolve(open_ev, now, "Service stopped flapping.")
        return (0, 1)
    return (0, 0)


def _resolve_all(agent, name, now):
    n = 0
    for at in ("service_down", "service_flapping"):
        ev = _open_event(agent, name, at)
        if ev:
            _resolve(ev, now, "No longer a watched service.")
            n += 1
    return n


def reconcile_watched_services(agent, reported) -> dict:
    """`reported` = the agent's metrics['watched_services'] (rich ServiceStat dicts:
    {name, state, running, …}). Update WatchedServiceStatus + fire/resolve down &
    flap alerts. Returns {fired, resolved}."""
    now = timezone.now()
    fired = resolved = 0
    seen = set()

    for svc in reported or []:
        if not isinstance(svc, dict):
            continue
        name = (svc.get("name") or "").strip()
        if not name:
            continue
        seen.add(name)
        running = bool(svc.get("running"))
        state = (svc.get("state") or "")[:32]

        ws, created = WatchedServiceStatus.objects.get_or_create(agent=agent, name=name)
        prev = None if (created or ws.collected_at is None) else ws.running
        if prev is True and not running:          # transition down
            ws.down_since = now
            ws.last_change_at = now
        elif prev is False and running:           # came back → a restart/flap
            ws.last_change_at = now
            ws.down_since = None
            restarts = _trim(ws.restarts, now, 24 * 3600)
            restarts.append(now.isoformat())
            ws.restarts = restarts
        elif prev is None:                        # first observation = baseline
            ws.last_change_at = now
            ws.down_since = now if not running else None
        ws.running = running
        ws.state = state
        ws.collected_at = now
        ws.save()

        f1, r1 = _reconcile_down(agent, ws, now)
        f2, r2 = _reconcile_flap(agent, ws, now)
        fired += f1 + f2
        resolved += r1 + r2

    # Services removed from the watch list (no longer reported) → resolve their
    # alerts and drop the rows so a de-watched service stops alerting.
    for ws in agent.watched_services.exclude(name__in=seen):
        resolved += _resolve_all(agent, ws.name, now)
        ws.delete()

    return {"fired": fired, "resolved": resolved}
