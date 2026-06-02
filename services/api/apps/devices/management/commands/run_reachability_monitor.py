"""
Device reachability monitor.

Every --interval seconds, concurrently TCP-connect to each active/unreachable
device's management port (22) and update its liveness:

- reachable   → is_reachable=True, last_seen=now, consecutive_failures=0; if the
                device was 'unreachable', flip it back to 'active' and emit an
                info alert.
- unreachable → consecutive_failures += 1; at 3 consecutive failures flip an
                'active' device to 'unreachable' and emit a high alert.

Heartbeat fields are written with .update() (no post_save signals, so the SNMP
poller isn't re-published every cycle); status transitions additionally publish
to NATS netpulse.alerts.<severity>.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time

from django.core.management.base import BaseCommand

logger = logging.getLogger(__name__)

FAILURE_THRESHOLD = 3
SSH_PORT = 22

# Ping/RTT latency alerting thresholds (env-overridable). A device must exceed
# the threshold for N consecutive checks before the alert escalates, so a single
# slow probe doesn't fire. Names match the seeded system AlertRules.
LATENCY_WARN_MS = float(os.environ.get("PING_LATENCY_WARN_MS", "100"))
LATENCY_WARN_CHECKS = int(os.environ.get("PING_LATENCY_WARN_CHECKS", "3"))
LATENCY_CRIT_MS = float(os.environ.get("PING_LATENCY_CRIT_MS", "500"))
LATENCY_CRIT_CHECKS = int(os.environ.get("PING_LATENCY_CRIT_CHECKS", "2"))
LATENCY_RULE_WARN = "High Ping Latency"
LATENCY_RULE_CRIT = "Ping Latency Critical"


def classify_latency(rtt_ms: float | None) -> str:
    """Bucket an RTT sample: 'crit' > crit threshold, 'warn' > warn threshold, else 'ok'."""
    if rtt_ms is None:
        return "ok"  # unreachable is handled by the failure path, not latency
    if rtt_ms > LATENCY_CRIT_MS:
        return "crit"
    if rtt_ms > LATENCY_WARN_MS:
        return "warn"
    return "ok"


class Command(BaseCommand):
    help = "Periodically check device reachability (TCP/22) and update status + alerts."

    def add_arguments(self, parser):
        parser.add_argument("--interval", type=int, default=30)
        parser.add_argument("--timeout", type=float, default=5.0)
        parser.add_argument("--once", action="store_true", help="Run a single cycle and exit.")

    def handle(self, *args, **options):
        # Per-device latency-alert state ({device_id: {"warn", "crit", "level"}}),
        # kept across cycles so we alert only on escalation, not every check.
        self._lat_state: dict = {}
        self._influx = self._connect_influx()
        try:
            asyncio.run(self._run(options["interval"], options["timeout"], options["once"]))
        finally:
            if self._influx:
                try:
                    self._influx[1].close(); self._influx[0].close()
                except Exception:
                    pass

    async def _run(self, interval: int, timeout: float, once: bool):
        logger.info("reachability-monitor starting (interval=%ds, timeout=%ss)", interval, timeout)
        while True:
            try:
                await self._cycle(timeout)
            except Exception as exc:  # never let one cycle kill the loop
                logger.error("reachability cycle error: %s", exc)
            if once:
                return
            await asyncio.sleep(interval)

    # ── InfluxDB ────────────────────────────────────────────────────────────────

    @staticmethod
    def _connect_influx():
        """Return (InfluxDBClient, WriteApi) for reachability points, or None on error."""
        from django.conf import settings
        try:
            from influxdb_client import InfluxDBClient
            from influxdb_client.client.write_api import ASYNCHRONOUS

            client = InfluxDBClient(
                url=settings.INFLUXDB_URL, token=settings.INFLUXDB_TOKEN, org=settings.INFLUXDB_ORG)
            return client, client.write_api(write_options=ASYNCHRONOUS)
        except Exception as exc:
            logger.warning("reachability-monitor: InfluxDB unavailable (%s) — RTT not stored", exc)
            return None

    def _write_reachability(self, results) -> None:
        """Write one device_reachability point per checked device (best-effort)."""
        if not self._influx:
            return
        from django.conf import settings
        try:
            from influxdb_client import Point

            points = []
            for d, ok, _method, rtt_ms in results:
                p = (Point("device_reachability")
                     .tag("device_id", str(d["id"]))
                     .tag("hostname", d.get("hostname") or "")
                     .field("is_reachable", 1 if ok else 0))
                if ok and rtt_ms is not None:
                    p = p.field("rtt_ms", float(rtt_ms))
                points.append(p)
            self._influx[1].write(bucket=settings.INFLUXDB_BUCKET, record=points)
        except Exception as exc:
            logger.warning("reachability-monitor: InfluxDB write failed: %s", exc)

    async def _cycle(self, timeout: float):
        from asgiref.sync import sync_to_async

        devices = await sync_to_async(self._fetch_devices)()
        if not devices:
            return
        results = await asyncio.gather(*[self._check(d, timeout) for d in devices])
        # Store RTT/liveness history for charting (best-effort, non-blocking).
        self._write_reachability(results)
        transitions = await sync_to_async(self._apply_all)(results)
        for sev, hostname, device_id, msg in transitions:
            await self._publish_alert(sev, hostname, device_id, msg)
            # Real-time UI push: reachable transitions are info, others unreachable.
            await self._push_ws({
                "device_id": device_id, "hostname": hostname,
                "is_reachable": sev == "info",
                "status": "active" if sev == "info" else "unreachable",
                "message": msg,
            })
        # Latency-spike alerts (separate from up/down — a device can be reachable
        # but slow). Emitted on escalation only; respects maintenance windows.
        for sev, hostname, device_id, rule, msg in await sync_to_async(self._latency_alerts)(results):
            await self._publish_alert(sev, hostname, device_id, msg, rule_name=rule)
        reachable = sum(1 for _, ok, _, _ in results if ok)
        logger.info("reachability: %d/%d devices reachable", reachable, len(results))

    def _latency_alerts(self, results) -> list[tuple]:
        """
        Update per-device latency state and return escalation alerts as
        (severity, hostname, device_id, rule_name, message). Fires once when a
        device crosses into 'warn' (medium) or 'crit' (high) after the required
        consecutive over-threshold checks, and once (info) on recovery to 'ok'.
        """
        from apps.alerting.maintenance import is_in_maintenance

        alerts: list[tuple] = []
        for d, ok, _method, rtt_ms in results:
            if not ok:
                # Unreachable: reset latency state (the down alert covers it).
                self._lat_state.pop(d["id"], None)
                continue
            st = self._lat_state.setdefault(d["id"], {"warn": 0, "crit": 0, "level": "ok"})
            bucket = classify_latency(rtt_ms)
            if bucket == "crit":
                st["crit"] += 1; st["warn"] += 1
            elif bucket == "warn":
                st["warn"] += 1; st["crit"] = 0
            else:
                st["warn"] = 0; st["crit"] = 0

            new_level = st["level"]
            if st["crit"] >= LATENCY_CRIT_CHECKS:
                new_level = "crit"
            elif st["warn"] >= LATENCY_WARN_CHECKS:
                new_level = "warn"
            elif bucket == "ok":
                new_level = "ok"
            if new_level == st["level"]:
                continue
            prev, st["level"] = st["level"], new_level
            if new_level == "crit":
                if not is_in_maintenance(device_id=d["id"], severity="high"):
                    alerts.append(("high", d["hostname"], d["id"], LATENCY_RULE_CRIT,
                                   f"{d['hostname']} ping latency critical: {rtt_ms:.1f}ms"))
            elif new_level == "warn":
                if not is_in_maintenance(device_id=d["id"], severity="medium"):
                    alerts.append(("medium", d["hostname"], d["id"], LATENCY_RULE_WARN,
                                   f"{d['hostname']} ping latency high: {rtt_ms:.1f}ms"))
            elif new_level == "ok" and prev != "ok":
                alerts.append(("info", d["hostname"], d["id"], LATENCY_RULE_WARN,
                               f"{d['hostname']} ping latency back to normal: {rtt_ms:.1f}ms"))
        return alerts

    async def _push_ws(self, payload: dict):
        """Send a device_status event to the 'devices' channel group (best-effort)."""
        try:
            from channels.layers import get_channel_layer
            layer = get_channel_layer()
            if layer is not None:
                await layer.group_send("devices", {"type": "device_status", "payload": payload})
        except Exception as exc:
            logger.warning("device_status WS push failed: %s", exc)

    # ── data access (sync) ────────────────────────────────────────────────────

    @staticmethod
    def _fetch_devices() -> list[dict]:
        from apps.devices.models import Device
        return list(
            Device.objects.filter(status__in=[Device.Status.ACTIVE, Device.Status.UNREACHABLE])
            .values("id", "hostname", "management_ip", "ip_address", "status", "consecutive_failures")
        )

    def _apply_all(self, results) -> list[tuple]:
        from django.utils import timezone
        from apps.devices.models import Device

        now = timezone.now()
        transitions: list[tuple] = []
        for d, ok, method, _rtt_ms in results:
            prev_status = d["status"]
            if ok:
                new_status = Device.Status.ACTIVE if prev_status == Device.Status.UNREACHABLE else prev_status
                Device.objects.filter(pk=d["id"]).update(
                    is_reachable=True, last_seen=now, last_reachability_check=now,
                    reachability_method=method, consecutive_failures=0, status=new_status,
                    unreachable_since=None,
                )
                if prev_status == Device.Status.UNREACHABLE:
                    transitions.append(("info", d["hostname"], d["id"], f"Device {d['hostname']} reachable again"))
                    # Auto-resolve the firing reachability alert(s) for this device.
                    from apps.alerts.resolve import resolve_matching
                    resolve_matching(note=f"Device {d['hostname']} became reachable",
                                     now=now, source="reachability_monitor", device_id=d["id"])
            else:
                fails = (d["consecutive_failures"] or 0) + 1
                updates = dict(is_reachable=False, last_reachability_check=now,
                               reachability_method=method, consecutive_failures=fails)
                if fails >= FAILURE_THRESHOLD and prev_status == Device.Status.ACTIVE:
                    updates["status"] = Device.Status.UNREACHABLE
                    # Stamp the start of the outage for downtime reporting.
                    updates["unreachable_since"] = now
                    # Suppress the alert during a maintenance window (still flip status).
                    from apps.alerting.maintenance import is_in_maintenance
                    if not is_in_maintenance(device_id=d["id"], severity="high"):
                        transitions.append(("high", d["hostname"], d["id"], f"Device {d['hostname']} unreachable"))
                Device.objects.filter(pk=d["id"]).update(**updates)
        return transitions

    # ── checks ─────────────────────────────────────────────────────────────────

    async def _check(self, d: dict, timeout: float):
        """TCP-connect to port 22. Returns (device, reachable_bool, method, rtt_ms)."""
        host = d.get("management_ip") or d.get("ip_address")
        if not host:
            return d, False, "tcp", None
        start = time.monotonic()
        try:
            fut = asyncio.open_connection(host, SSH_PORT)
            reader, writer = await asyncio.wait_for(fut, timeout=timeout)
            rtt_ms = (time.monotonic() - start) * 1000
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
            return d, True, "tcp", rtt_ms
        except Exception:
            return d, False, "tcp", None

    # ── alerts ─────────────────────────────────────────────────────────────────

    async def _publish_alert(self, severity: str, hostname: str, device_id, message: str,
                             rule_name: str = "device-unreachable"):
        import nats  # lazy
        try:
            nc = await nats.connect(
                os.environ.get("NATS_URL", "nats://nats:4222"),
                user=os.environ.get("NATS_USER") or None,
                password=os.environ.get("NATS_PASSWORD") or None,
                connect_timeout=3,
            )
        except Exception as exc:
            logger.warning("reachability alert publish failed (connect): %s", exc)
            return
        try:
            payload = {
                "source": "reachability_monitor", "rule_name": rule_name,
                "device_id": device_id,
                "hostname": hostname, "severity": severity,
                "title": message, "message": message,
            }
            await nc.publish(f"netpulse.alerts.{severity}", json.dumps(payload).encode())
            await nc.flush()
        finally:
            await nc.drain()
