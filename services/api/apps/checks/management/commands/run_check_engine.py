"""
run_check_engine — the agentless service-check runner.

Every tick it finds checks whose interval has elapsed, probes them
concurrently (capped), records each result, advances the check state machine
and publishes NATS alerts on status changes:

    up   → down       netpulse.alerts.high     (after failures_before_alert)
    down → up         netpulse.alerts.info     (recovery)
    *    → degraded   netpulse.alerts.medium   (slow response)

Alert suppression: a ``down`` alert is skipped when the check's associated
device is itself unreachable — the service being down is expected and the
device alert already covers it.
"""
import asyncio
import json
import logging
import os
import signal

from asgiref.sync import sync_to_async
from django.core.management.base import BaseCommand

from apps.checks.runner import alert_enabled, run_check
from apps.checks.service import check_to_dict, persist_result

logger = logging.getLogger(__name__)

ALERT_SEVERITY = {"down": "high", "recovery": "info", "degraded": "medium"}


class Command(BaseCommand):
    help = "Run due service checks, record results and alert on status changes."

    def add_arguments(self, parser):
        parser.add_argument("--tick", type=float, default=1.0, help="Scheduler loop interval (s).")
        parser.add_argument("--max-concurrent", type=int, default=50)
        parser.add_argument("--once", action="store_true", help="Run one tick and exit.")

    def handle(self, *args, **options):
        asyncio.run(self._run(options["tick"], options["max_concurrent"], options["once"]))

    async def _run(self, tick: float, max_concurrent: int, once: bool):
        stop = asyncio.Event()
        try:
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGTERM, signal.SIGINT):
                loop.add_signal_handler(sig, stop.set)
        except (NotImplementedError, ValueError):
            pass

        self._sem = asyncio.Semaphore(max_concurrent)
        logger.info("check-engine started (tick=%ss, max_concurrent=%d)", tick, max_concurrent)
        while not stop.is_set():
            try:
                await self._cycle()
            except Exception as exc:
                logger.error("check-engine cycle error: %s", exc)
            if once:
                return
            try:
                await asyncio.wait_for(stop.wait(), timeout=tick)
            except asyncio.TimeoutError:
                pass
        logger.info("check-engine stopped cleanly")

    async def _cycle(self):
        due = await sync_to_async(self._fetch_due, thread_sensitive=True)()
        if not due:
            return
        await asyncio.gather(*(self._run_one(c) for c in due))

    @staticmethod
    def _fetch_due() -> list:
        """Return ServiceCheck rows whose interval has elapsed."""
        from datetime import timedelta

        from django.utils import timezone

        from apps.checks.models import ServiceCheck

        now = timezone.now()
        due = []
        qs = ServiceCheck.objects.filter(is_active=True, is_enabled=True).select_related("device")
        for c in qs:
            if c.last_checked is None or c.last_checked + timedelta(seconds=c.interval_seconds) <= now:
                due.append(c)
        return due

    async def _run_one(self, check):
        async with self._sem:
            result = await run_check(check_to_dict(check))
        alert = await sync_to_async(self._persist, thread_sensitive=True)(check, result)
        if alert == "recovery":
            await sync_to_async(self._resolve_alerts, thread_sensitive=True)(check)
        if alert:
            await self._maybe_alert(check, result, alert)

    @staticmethod
    def _resolve_alerts(check):
        from apps.alerts.resolve import resolve_matching
        resolve_matching(note=f"Service check {check.name} recovered",
                         source="check_engine", check_id=check.id)

    @staticmethod
    def _suppressed(check, alert) -> bool:
        from apps.alerting.maintenance import is_in_maintenance
        sev = {"down": "high", "recovery": "info", "degraded": "medium"}.get(alert)
        return is_in_maintenance(device_id=check.device_id, severity=sev, check_type=check.check_type)

    @staticmethod
    def _persist(check, result) -> str | None:
        from django.utils import timezone

        from apps.checks.collectors import engine_collector_for
        collector = engine_collector_for(check)
        return persist_result(check, result, timezone.now(), collector=collector)

    async def _maybe_alert(self, check, result, alert: str):
        # Respect the check's per-state alert toggles.
        if not alert_enabled(alert, check.alert_on_down, check.alert_on_recovery, check.alert_on_degraded):
            return
        # Suppress during a maintenance window covering this check's device.
        if await sync_to_async(self._suppressed, thread_sensitive=True)(check, alert):
            return
        # Suppress down alerts when the associated device is itself unreachable.
        if alert == "down" and check.device_id:
            if not await sync_to_async(self._device_reachable, thread_sensitive=True)(check.device_id):
                logger.info("check %s down but device %s unreachable — suppressing alert",
                            check.name, check.device_id)
                return
        severity = ALERT_SEVERITY.get(alert, "medium")
        target = f"{check.check_type}://{check.host}"
        if alert == "recovery":
            title = f"Service Recovered: {check.name} ({target})"
        elif alert == "degraded":
            title = f"Service Degraded: {check.name} ({target}) — {result.get('response_time_ms')}ms"
        else:
            # Name the collectors that detected the failure, for multi-vantage checks.
            vantage = await sync_to_async(self._failing_vantage, thread_sensitive=True)(check)
            detail = result.get('error') or 'check failed'
            title = f"Service Down: {check.name} ({target}) — {detail}{vantage}"
        await self._publish_alert(severity, check, title)

    @staticmethod
    def _failing_vantage(check) -> str:
        """A ' from N/M collectors: …' suffix when this check runs multi-vantage."""
        from apps.checks.collectors import failing_collector_names
        from apps.checks.models import ServiceCheckCollector

        total = ServiceCheckCollector.objects.filter(service_check=check, enabled=True).count()
        if total <= 1:
            return ""
        names = failing_collector_names(check)
        if not names:
            return ""
        return f" — failed from {len(names)}/{total} collectors: {', '.join(names)}"

    @staticmethod
    def _device_reachable(device_id) -> bool:
        from apps.devices.models import Device
        d = Device.objects.filter(pk=device_id).values("is_reachable").first()
        return bool(d and d["is_reachable"])

    async def _publish_alert(self, severity: str, check, title: str):
        import nats
        try:
            nc = await nats.connect(
                os.environ.get("NATS_URL", "nats://nats:4222"),
                user=os.environ.get("NATS_USER") or None,
                password=os.environ.get("NATS_PASSWORD") or None,
                connect_timeout=3,
            )
        except Exception as exc:
            logger.warning("check alert publish failed (connect): %s", exc)
            return
        try:
            payload = {
                "source": "check_engine",
                "rule_name": "service-check-failed",
                "check_id": check.id,
                "check_name": check.name,
                "device_id": check.device_id,
                "site_id": check.site_id,
                "severity": severity,
                "title": title,
                "message": title,
            }
            await nc.publish(f"netpulse.alerts.{severity}", json.dumps(payload).encode())
            await nc.flush()
        finally:
            await nc.drain()
