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

from django.core.management.base import BaseCommand

logger = logging.getLogger(__name__)

FAILURE_THRESHOLD = 3
SSH_PORT = 22


class Command(BaseCommand):
    help = "Periodically check device reachability (TCP/22) and update status + alerts."

    def add_arguments(self, parser):
        parser.add_argument("--interval", type=int, default=30)
        parser.add_argument("--timeout", type=float, default=5.0)
        parser.add_argument("--once", action="store_true", help="Run a single cycle and exit.")

    def handle(self, *args, **options):
        asyncio.run(self._run(options["interval"], options["timeout"], options["once"]))

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

    async def _cycle(self, timeout: float):
        from asgiref.sync import sync_to_async

        devices = await sync_to_async(self._fetch_devices)()
        if not devices:
            return
        results = await asyncio.gather(*[self._check(d, timeout) for d in devices])
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
        reachable = sum(1 for _, ok, _ in results if ok)
        logger.info("reachability: %d/%d devices reachable", reachable, len(results))

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
        for d, ok, method in results:
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
                    transitions.append(("high", d["hostname"], d["id"], f"Device {d['hostname']} unreachable"))
                Device.objects.filter(pk=d["id"]).update(**updates)
        return transitions

    # ── checks ─────────────────────────────────────────────────────────────────

    async def _check(self, d: dict, timeout: float):
        """TCP-connect to port 22. Returns (device, reachable_bool, method)."""
        host = d.get("management_ip") or d.get("ip_address")
        if not host:
            return d, False, "tcp"
        try:
            fut = asyncio.open_connection(host, SSH_PORT)
            reader, writer = await asyncio.wait_for(fut, timeout=timeout)
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
            return d, True, "tcp"
        except Exception:
            return d, False, "tcp"

    # ── alerts ─────────────────────────────────────────────────────────────────

    async def _publish_alert(self, severity: str, hostname: str, device_id, message: str):
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
                "source": "reachability_monitor", "device_id": device_id,
                "hostname": hostname, "severity": severity,
                "title": message, "message": message,
            }
            await nc.publish(f"netpulse.alerts.{severity}", json.dumps(payload).encode())
            await nc.flush()
        finally:
            await nc.drain()
