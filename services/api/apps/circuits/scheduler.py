"""Periodic WAN-circuit checks: utilization threshold + contract expiry.

Wired into run_scheduler. Utilization needs InfluxDB (best-effort, skipped when
unavailable); contract expiry is pure date math.
"""
from __future__ import annotations

import logging

from django.utils import timezone

logger = logging.getLogger(__name__)

_UTIL_RULE_NAME = "High WAN Utilization"
_CONTRACT_RULE_NAME = "WAN Contract Expiring"
_CONTRACT_DAYS = (90, 60, 30, 14, 7)


def _rule(name, severity, description, condition):
    from apps.alerts.models import AlertRule
    rule, _ = AlertRule.objects.get_or_create(
        name=name,
        defaults={"description": description, "severity": severity,
                  "condition": condition, "cooldown_minutes": 0, "is_system": True},
    )
    return rule


# ── Utilization threshold (standing, auto-resolving) ─────────────────────────
def _reconcile_util_alert(circuit, util: dict | None) -> bool:
    from apps.alerts.models import AlertEvent, AlertRule

    open_qs = AlertEvent.objects.filter(
        state=AlertEvent.State.FIRING,
        labels__alert_type="wan_utilization", labels__circuit_id=circuit.id)

    pcts = []
    cur = (util or {}).get("current") or {}
    for k in ("rx_pct", "tx_pct"):
        if isinstance(cur.get(k), (int, float)):
            pcts.append(cur[k])
    over = pcts and max(pcts) > circuit.alert_threshold_pct
    if not over:
        for ev in open_qs:
            ev.state = AlertEvent.State.RESOLVED
            ev.resolved_at = timezone.now()
            ev.resolution_note = "WAN utilization back within threshold."
            ev.save(update_fields=["state", "resolved_at", "resolution_note"])
        return False
    if open_qs.exists():
        return False
    rx = cur.get("rx_mbps")
    pct = max(pcts)
    AlertEvent.objects.create(
        rule=_rule(_UTIL_RULE_NAME, AlertRule.Severity.MEDIUM,
                   "A WAN circuit's utilization exceeded its alert threshold.",
                   {"rule_type": "wan_utilization"}),
        state=AlertEvent.State.FIRING,
        labels={"source": "circuits", "alert_type": "wan_utilization",
                "circuit_id": circuit.id, "severity": "warning",
                "hostname": circuit.device.hostname if circuit.device_id else "",
                "interface": circuit.interface, "rx_pct": str(pct)},
        annotations={
            "title": f"High WAN utilization: {circuit.name}",
            "message": (f"{circuit.name} is at {pct}%"
                        + (f" ({rx} Mbps of {circuit.bandwidth_mbps} Mbps)"
                           if rx is not None and circuit.bandwidth_mbps else "")),
            "severity": "warning"},
    )
    return True


# ── Contract expiry (one alert per circuit+day-bucket) ───────────────────────
def check_contract_expiry(today=None) -> int:
    from apps.alerts.models import AlertEvent, AlertRule

    from .models import WanCircuit
    today = today or timezone.now().date()
    fired = 0
    qs = WanCircuit.objects.exclude(contract_end_date__isnull=True).exclude(
        status=WanCircuit.Status.CANCELLED)
    for c in qs:
        days = (c.contract_end_date - today).days
        bucket = next((d for d in _CONTRACT_DAYS if days == d), None)
        if bucket is None:
            continue
        exists = AlertEvent.objects.filter(
            labels__alert_type="wan_contract", labels__circuit_id=c.id,
            labels__days_bucket=str(bucket)).exists()
        if exists:
            continue
        AlertEvent.objects.create(
            rule=_rule(_CONTRACT_RULE_NAME, AlertRule.Severity.MEDIUM,
                       "A WAN circuit contract is approaching its end date.",
                       {"rule_type": "wan_contract"}),
            state=AlertEvent.State.FIRING,
            labels={"source": "circuits", "alert_type": "wan_contract",
                    "circuit_id": c.id, "days_bucket": str(bucket), "severity": "warning"},
            annotations={
                "title": f"WAN contract expiring: {c.name}",
                "message": (f"{c.name} ({c.provider}) contract expires in {days} day(s) "
                            f"on {c.contract_end_date}."),
                "severity": "warning"},
        )
        fired += 1
    return fired


def check_utilization() -> int:
    """Reconcile utilization alerts for bound active circuits. Returns # firing."""
    from .models import WanCircuit
    from .utilization import get_circuit_utilization
    fired = 0
    qs = (WanCircuit.objects.filter(status=WanCircuit.Status.ACTIVE)
          .exclude(device__isnull=True).exclude(interface="").select_related("device"))
    for c in qs:
        try:
            util = get_circuit_utilization(c, period="1h")
            if _reconcile_util_alert(c, util):
                fired += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning("circuit %s utilization check failed: %s", c.id, exc)
    return fired


def run_circuit_checks() -> dict:
    return {"contract_alerts": check_contract_expiry(), "util_alerts": check_utilization()}
