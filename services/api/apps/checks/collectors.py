"""Multi-collector service-check resolution + result aggregation.

A check can run from several vantage points (collectors). `collectors_for_check`
decides which collectors should run a check; `evaluate_check_status` rolls the
per-collector results up into one status using the check's collector_mode;
`record_collector_result` stores one vantage point's result and refreshes the
aggregate.
"""
from __future__ import annotations

from .models import ServiceCheck, ServiceCheckCollector

# Probe status (up/down/degraded/unknown) → per-collector pass/fail bucket.
# Degraded is still "reachable", so it counts as passing for aggregation.
def _result_bucket(probe_status: str) -> str:
    if probe_status == ServiceCheck.Status.DOWN:
        return ServiceCheckCollector.Result.FAILING
    if probe_status == ServiceCheck.Status.UNKNOWN:
        return ServiceCheckCollector.Result.UNKNOWN
    return ServiceCheckCollector.Result.PASSING


def collectors_for_check(check, device=None):
    """Collectors that should run `check`, per its collector_mode.

    Returns a Collector queryset (possibly empty). `device` defaults to the
    check's own device.
    """
    from apps.collectors.models import Collector

    active = Collector.objects.filter(status=Collector.Status.ACTIVE)
    mode = check.collector_mode
    device = device if device is not None else check.device

    if mode == ServiceCheck.CollectorMode.ALL or mode == ServiceCheck.CollectorMode.ANY:
        return active
    if mode == ServiceCheck.CollectorMode.SELECTED:
        return active.filter(
            check_assignments__service_check=check,
            check_assignments__enabled=True,
        ).distinct()
    if mode == ServiceCheck.CollectorMode.SITE:
        if device is not None and device.site_id:
            site_collectors = active.filter(site_id=device.site_id)
            if site_collectors.exists():
                return site_collectors
        # Fall back to the default collector when the site has none.
        return active.filter(is_default=True)
    return active.filter(is_default=True)


def evaluate_check_status(check) -> str:
    """Aggregate per-collector results into one ServiceCheck.Status.

    - all:  up only if every (enabled) collector passes; down if any fails.
    - any:  up if any collector passes; otherwise down.
    - selected/site: majority rules — down when >50% of reporting collectors fail.

    Unknown-only (nothing has reported yet) → unknown.
    """
    rows = list(
        ServiceCheckCollector.objects.filter(service_check=check, enabled=True)
        .values_list("last_result", flat=True)
    )
    statuses = [s for s in rows]
    if not statuses or all(s == ServiceCheckCollector.Result.UNKNOWN for s in statuses):
        return ServiceCheck.Status.UNKNOWN

    passing = ServiceCheckCollector.Result.PASSING
    failing = ServiceCheckCollector.Result.FAILING
    mode = check.collector_mode

    if mode == ServiceCheck.CollectorMode.ALL:
        if any(s == failing for s in statuses):
            return ServiceCheck.Status.DOWN
        if all(s == passing for s in statuses):
            return ServiceCheck.Status.UP
        return ServiceCheck.Status.DEGRADED  # mix of passing + unknown

    if mode == ServiceCheck.CollectorMode.ANY:
        return ServiceCheck.Status.UP if any(s == passing for s in statuses) else ServiceCheck.Status.DOWN

    # selected / site → majority of the collectors that actually reported.
    reported = [s for s in statuses if s != ServiceCheckCollector.Result.UNKNOWN]
    if not reported:
        return ServiceCheck.Status.UNKNOWN
    failed = sum(1 for s in reported if s == failing)
    return ServiceCheck.Status.DOWN if failed / len(reported) > 0.5 else ServiceCheck.Status.UP


def record_collector_result(check, collector, result: dict, now) -> ServiceCheckCollector:
    """Upsert the per-collector row for one vantage point's probe result."""
    bucket = _result_bucket(result["status"])
    sca, _ = ServiceCheckCollector.objects.get_or_create(
        service_check=check, collector=collector,
        defaults={"enabled": True},
    )
    sca.last_result = bucket
    sca.last_checked = now
    sca.last_latency_ms = result.get("response_time_ms")
    sca.last_error = (result.get("error") or "")[:512]
    if bucket == ServiceCheckCollector.Result.FAILING:
        sca.consecutive_failures += 1
    else:
        sca.consecutive_failures = 0
    sca.save(update_fields=[
        "last_result", "last_checked", "last_latency_ms", "last_error",
        "consecutive_failures", "updated_at",
    ])
    return sca


def engine_collector_for(check):
    """The local/default collector the central engine should attribute a probe
    to for `check`, or None when the check shouldn't be recorded against the
    local collector (so the legacy single-location path is used instead).

    Today the central check-engine is the only executing vantage point, so it
    records as the default collector when that collector is one of the check's
    resolved collectors. Remote pollers will report their own results directly.
    """
    from apps.collectors.models import Collector

    local = Collector.objects.filter(
        is_default=True, status=Collector.Status.ACTIVE).first()
    if not local:
        return None
    resolved = set(collectors_for_check(check).values_list("id", flat=True))
    return local if local.id in resolved else None


def failing_collector_names(check) -> list[str]:
    """Names of the collectors currently reporting this check as failing."""
    return list(
        ServiceCheckCollector.objects.filter(
            service_check=check, enabled=True,
            last_result=ServiceCheckCollector.Result.FAILING,
        ).values_list("collector__name", flat=True)
    )
