"""A disabled AlertRule must fire nothing — even from engines that create
AlertEvents directly.

The stream-processor checks ``rule.is_active`` before creating an event, but the
"standing alert" engines — agents (liveness / stability / functional), circuits,
environment PoE, hostname-change, OS-policy, compliance startup, and the
reachability host-unreachable path — ``get_or_create`` their own built-in rule
and then create the FIRING ``AlertEvent`` directly, bypassing that check. They
call :func:`rule_enabled` first so that DISABLING a built-in genuinely stops its
alerts.

Why the disable STICKS against the engine: ``get_or_create`` applies its
``defaults`` only when it CREATES the row — an existing (operator-disabled) rule
is found and returned unchanged, so ``is_active=False`` is preserved across
engine runs. Combined with this gate, a disabled built-in stays disabled and
emits no new events. Resolve / auto-resolve paths are intentionally unaffected —
clearing a stale firing event is always safe.
"""
from __future__ import annotations


def rule_enabled(rule) -> bool:
    """False when an operator has disabled the rule (``is_active=False``)."""
    return bool(rule and rule.is_active)
