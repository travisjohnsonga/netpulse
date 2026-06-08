"""Glue between ServiceCheck rows and the async runner — kept sync/ORM-only."""
from __future__ import annotations

from .models import CheckResult, ServiceCheck
from .runner import next_state


def check_to_dict(check: ServiceCheck) -> dict:
    """Flatten a ServiceCheck into the plain dict the runner handlers expect."""
    return {
        "id": check.id,
        "check_type": check.check_type,
        "host": check.host,
        "effective_port": check.effective_port,
        "timeout_seconds": check.timeout_seconds,
        "config": check.config or {},
        "response_time_warning_ms": check.response_time_warning_ms,
        "response_time_critical_ms": check.response_time_critical_ms,
    }


def persist_result(check: ServiceCheck, result: dict, now, collector=None) -> str | None:
    """
    Record a probe result and advance the check's state machine.

    Writes a CheckResult, updates the check's current_status / failure counter /
    timestamps via :func:`next_state`, and returns the alert kind to raise
    (``down``/``recovery``/``degraded``) or ``None``.

    When `collector` is given, the result is attributed to that vantage point:
    its per-collector ServiceCheckCollector row is refreshed and the check's
    aggregate status is recomputed from ALL collectors (via the check's
    collector_mode) before driving the alert state machine. With no collector
    the legacy single-location behaviour is unchanged.
    """
    if collector is not None:
        from .collectors import evaluate_check_status, record_collector_result
        record_collector_result(check, collector, result, now)
        effective_status = evaluate_check_status(check)
    else:
        effective_status = result["status"]

    effective, failures, alert = next_state(
        check.current_status, check.consecutive_failures,
        effective_status, check.failures_before_alert,
    )

    CheckResult.objects.create(
        service_check=check,
        collector=collector,
        status=result["status"],
        response_time_ms=result.get("response_time_ms"),
        checked_at=now,
        error=(result.get("error") or "")[:512],
        details=result.get("details") or {},
    )

    changed = effective != check.current_status
    check.current_status = effective
    check.consecutive_failures = failures
    check.last_checked = now
    if changed:
        check.last_status_change = now
    check.save(update_fields=[
        "current_status", "consecutive_failures", "last_checked",
        "last_status_change", "updated_at",
    ])
    return alert
