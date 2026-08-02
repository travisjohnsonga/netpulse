"""Periodic agent liveness alerting.

Agents are outbound reporters (they push metrics over mTLS; nothing probes
them), so unlike network devices they were never in any liveness-alert loop —
``Agent.is_online`` was a display-only property. This task closes that gap: it
watches ``last_seen`` and fires/resolves an "agent offline" alert through the
SAME ``AlertEvent`` plumbing device-unreachable uses, so it shows up in Recent
Alerts / the Alerts page / notifications.

Behaviour:
  - One standing alert per agent (debounced): while an agent stays offline the
    alert is NOT re-fired each tick.
  - Auto-resolves when the agent reports again (last_seen fresh) — like the
    device-unreachable resolve.
  - Threshold is the agent's effective offline window (per-agent override else
    the global AGENT_OFFLINE_SECONDS) — the same value the online badge uses.
  - ``liveness_alerts_enabled=False`` suppresses (and resolves any open alert),
    for hosts that legitimately sleep (the lab) — without loosening the global
    default.

Wired into run_scheduler (``agent_liveness`` task).
"""
from __future__ import annotations

import logging

from django.utils import timezone

logger = logging.getLogger(__name__)

_RULE_NAME = "Agent Offline"


def _device_label(agent):
    # device_id links the alert to its server (Device column / Recent Alerts /
    # device-scoped views) — not just by hostname in the title.
    return {"device_id": agent.device_id} if agent.device_id else {}


def _offline_rule():
    from apps.alerts.models import AlertRule
    rule, _ = AlertRule.objects.get_or_create(
        name=_RULE_NAME,
        defaults={
            "description": ("A monitoring agent stopped reporting (host down, "
                            "unreachable, powered off, or the agent service stopped)."),
            "severity": AlertRule.Severity.HIGH,
            "condition": {"rule_type": "agent_offline"},
            "cooldown_minutes": 0,
            "is_system": True,
        },
    )
    return rule


def _offline_for(agent, now) -> float:
    """Seconds since the agent last checked in. Falls back to created_at so a
    freshly-enrolled agent that never reported still gets a grace period equal to
    its threshold before alerting (not an instant alert on enrollment)."""
    ref = agent.last_seen or agent.created_at
    return (now - ref).total_seconds()


def reconcile_agent_liveness() -> dict:
    """Fire/resolve agent-offline alerts. Returns {fired, resolved, offline}."""
    from apps.agents.models import Agent
    from apps.alerts.models import AlertEvent

    now = timezone.now()
    fired = resolved = offline = 0
    rule = None

    agents = Agent.objects.filter(status=Agent.Status.ACTIVE).select_related("device")
    for agent in agents:
        open_ev = AlertEvent.objects.filter(
            state=AlertEvent.State.FIRING,
            labels__alert_type="agent_offline",
            labels__agent_id=str(agent.id),
        ).first()

        is_offline = _offline_for(agent, now) > agent.offline_after_seconds()
        should_alert = is_offline and agent.liveness_alerts_enabled
        if is_offline:
            offline += 1

        if should_alert:
            if open_ev is None:  # debounce: only fire on the transition to offline
                if rule is None:
                    rule = _offline_rule()
                mins = int(_offline_for(agent, now) // 60)
                last = (agent.last_seen.isoformat() if agent.last_seen else "never")
                AlertEvent.objects.create(
                    rule=rule,
                    state=AlertEvent.State.FIRING,
                    labels={
                        "source": "agent_liveness", "alert_type": "agent_offline",
                        "agent_id": str(agent.id), "severity": "critical",
                        "hostname": agent.hostname, **_device_label(agent),
                    },
                    annotations={
                        "title": f"Agent offline: {agent.hostname}",
                        "message": (f"Agent on {agent.hostname} has not reported for "
                                    f"~{mins} min (threshold {agent.offline_after_seconds()}s; "
                                    f"last seen {last}). Host down, unreachable, powered "
                                    f"off, or the agent service stopped."),
                        "severity": "critical",
                    },
                )
                fired += 1
        elif open_ev is not None:
            # Online again, or alerts disabled → resolve the standing alert.
            open_ev.state = AlertEvent.State.RESOLVED
            open_ev.resolved_at = now
            open_ev.resolution_note = (
                "Liveness alerting disabled for this agent."
                if (is_offline and not agent.liveness_alerts_enabled)
                else "Agent resumed reporting.")
            open_ev.save(update_fields=["state", "resolved_at", "resolution_note"])
            resolved += 1

    return {"fired": fired, "resolved": resolved, "offline": offline}
