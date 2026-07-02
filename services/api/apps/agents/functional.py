"""Functional health alerting (Stage 1: web role).

The agent reports per-URL functional results (health gradient + cert days) for a
role on each role-check. This fires/resolves alerts through the existing
``AlertEvent`` plumbing (debounce + auto-resolve, like stability/agent-offline):

  - site **down** (no response/timeout)           → HIGH
  - site **degraded** (5xx)                        → MEDIUM
  - **certificate expiring** (≤ FUNCTIONAL_CERT_WARN_DAYS, >0) → MEDIUM
  - **certificate expired** (≤0)                   → HIGH

A 4xx "warning" (up, but check config/auth) is surfaced in the UI but does NOT
alert (it's serving). One alert per (agent, url, condition).
"""
from __future__ import annotations

import logging

from django.utils import timezone

from .models import FUNCTIONAL_CERT_WARN_DAYS

logger = logging.getLogger(__name__)


def _open(agent, url, alert_type):
    from apps.alerts.models import AlertEvent
    return AlertEvent.objects.filter(
        state=AlertEvent.State.FIRING, labels__alert_type=alert_type,
        labels__agent_id=str(agent.id), labels__url=url).first()


def _fire(agent, url, alert_type, severity, sev_label, title, message):
    from apps.alerts.gating import rule_enabled
    from apps.alerts.models import AlertEvent, AlertRule
    rule, _ = AlertRule.objects.get_or_create(
        name=title.split(":")[0],
        defaults={"description": message, "severity": severity,
                  "condition": {"rule_type": alert_type}, "cooldown_minutes": 0,
                  "is_system": True})
    if not rule_enabled(rule):
        return  # operator disabled the built-in → suppress new alerts
    labels = {"source": "functional", "alert_type": alert_type,
              "agent_id": str(agent.id), "url": url, "hostname": agent.hostname,
              "severity": sev_label}
    # device_id is the linkage key the rest of the system uses (Device column,
    # server Recent Alerts, device-scoped filtering) — set it so the alert shows
    # where it belongs, not just by hostname in the title.
    if agent.device_id:
        labels["device_id"] = agent.device_id
    AlertEvent.objects.create(
        rule=rule, state=AlertEvent.State.FIRING, labels=labels,
        annotations={"title": title, "message": message, "severity": sev_label})


def _resolve(agent, url, alert_type, now, note):
    ev = _open(agent, url, alert_type)
    if ev:
        ev.state = ev.State.RESOLVED
        ev.resolved_at = now
        ev.resolution_note = note
        ev.save(update_fields=["state", "resolved_at", "resolution_note"])
        return 1
    return 0


def reconcile_functional_health(agent, role, results) -> dict:
    """results = the agent's per-URL functional dicts for `role`:
    {url, health, status_code, latency_ms, cert_days_remaining, error}.
    Fire/resolve site + cert alerts. Returns {fired, resolved}."""
    from apps.alerts.models import AlertRule

    now = timezone.now()
    fired = resolved = 0
    seen = set()

    for r in results or []:
        if not isinstance(r, dict) or not r.get("url"):
            continue
        url = r["url"]
        seen.add(url)
        health = (r.get("health") or "").lower()
        host = agent.hostname

        # --- site down (HIGH) ---
        if health == "down":
            if _open(agent, url, "site_down") is None:
                _fire(agent, url, "site_down", AlertRule.Severity.HIGH, "critical",
                      f"Site down: {url}",
                      f"{url} did not respond on {host}"
                      + (f" ({r['error']})" if r.get("error") else "."))
                fired += 1
        else:
            resolved += _resolve(agent, url, "site_down", now, "Site is responding again.")

        # --- site degraded / 5xx (MEDIUM) ---
        if health == "degraded":
            if _open(agent, url, "site_degraded") is None:
                _fire(agent, url, "site_degraded", AlertRule.Severity.MEDIUM, "warning",
                      f"Site degraded: {url}",
                      f"{url} returned {r.get('status_code', '5xx')} on {host}.")
                fired += 1
        else:
            resolved += _resolve(agent, url, "site_degraded", now, "Site no longer returning 5xx.")

        # --- certificate expiry (HTTPS only; cert_days_remaining present) ---
        days = r.get("cert_days_remaining")
        if isinstance(days, int):
            if days <= 0:
                if _open(agent, url, "cert_expired") is None:
                    _fire(agent, url, "cert_expired", AlertRule.Severity.HIGH, "critical",
                          f"Certificate expired: {url}",
                          f"The TLS certificate for {url} on {host} has expired.")
                    fired += 1
                resolved += _resolve(agent, url, "cert_expiring", now, "Superseded by cert-expired.")
            elif days <= FUNCTIONAL_CERT_WARN_DAYS:
                if _open(agent, url, "cert_expiring") is None:
                    _fire(agent, url, "cert_expiring", AlertRule.Severity.MEDIUM, "warning",
                          f"Certificate expiring: {url}",
                          f"The TLS certificate for {url} on {host} expires in {days} day(s).")
                    fired += 1
                resolved += _resolve(agent, url, "cert_expired", now, "Certificate not expired.")
            else:
                resolved += _resolve(agent, url, "cert_expiring", now, "Certificate renewed.")
                resolved += _resolve(agent, url, "cert_expired", now, "Certificate renewed.")

    # URLs no longer reported (removed from config) → resolve their open alerts.
    from apps.alerts.models import AlertEvent
    stale = AlertEvent.objects.filter(
        state=AlertEvent.State.FIRING, labels__source="functional",
        labels__agent_id=str(agent.id)).exclude(labels__url__in=list(seen))
    for ev in stale:
        ev.state = AlertEvent.State.RESOLVED
        ev.resolved_at = now
        ev.resolution_note = "No longer a functional-check target."
        ev.save(update_fields=["state", "resolved_at", "resolution_note"])
        resolved += 1

    return {"fired": fired, "resolved": resolved}
