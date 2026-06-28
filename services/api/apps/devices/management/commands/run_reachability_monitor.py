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
# Liveness probe ports, tried in order. SSH first; HTTPS (443) as a fallback for
# devices that block SSH from the collector (firewall trusted-hosts).
REACHABILITY_PORTS = [22, 443]

# Ping/RTT latency alerting thresholds (env-overridable). A device must exceed
# the threshold for N consecutive checks before the alert escalates, so a single
# slow probe doesn't fire. Names match the seeded system AlertRules.
LATENCY_WARN_MS = float(os.environ.get("PING_LATENCY_WARN_MS", "100"))
LATENCY_WARN_CHECKS = int(os.environ.get("PING_LATENCY_WARN_CHECKS", "3"))
LATENCY_CRIT_MS = float(os.environ.get("PING_LATENCY_CRIT_MS", "500"))
LATENCY_CRIT_CHECKS = int(os.environ.get("PING_LATENCY_CRIT_CHECKS", "2"))
LATENCY_RULE_WARN = "High Ping Latency"
LATENCY_RULE_CRIT = "Ping Latency Critical"

# Loopback/synthetic probe hosts. A device whose effective probe address is one
# of these doesn't point at a real network target — it points at the collector
# itself (e.g. an agent-linked server whose Device record was self-healed to
# 127.0.0.1). TCP-probing it can only manufacture false "unreachable", so the
# monitor skips it. (Mirrors apps.agents.models._SELF_HOSTS.)
SYNTHETIC_HOSTS = {"127.0.0.1", "::1", "[::1]", "0.0.0.0", "localhost"}

HOST_UNREACHABLE_RULE = "Host Unreachable"


def is_pingable_ip(ip) -> bool:
    """True if ``ip`` is a real, routable target the collector can meaningfully
    probe — i.e. NOT loopback, link-local, unspecified, multicast, or the
    synthetic ULA placeholder (#118 `placeholder_ip`, fc00::/7). Private LAN
    ranges (10/8, 172.16/12, 192.168/16) ARE pingable on-site, so they pass.
    An agent whose only known IP fails this is reported "not network-probed"
    rather than a false "unreachable" (the #133 lesson)."""
    import ipaddress
    if not ip:
        return False
    try:
        addr = ipaddress.ip_address(str(ip).strip().strip("[]"))
    except ValueError:
        return False
    if addr.is_loopback or addr.is_link_local or addr.is_unspecified or addr.is_multicast:
        return False
    # fc00::/7 is the ULA space the agent device-link uses for synthetic IPs —
    # valid + unique for the DB but never routable to a real host.
    if isinstance(addr, ipaddress.IPv6Address) and addr.is_private and not addr.ipv4_mapped:
        return False
    return True


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
        # Per-agent consecutive-failure counts for the host-unreachable alert
        # (in-memory, like _lat_state — the standing AlertEvent is the durable
        # debounce; this just gates the FAILURE_THRESHOLD before firing).
        self._agent_fails: dict = {}
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
        # On the first cycle after (re)start, broadcast every device's current
        # reachability — not just transitions — so UIs that loaded a stale
        # last_seen/status while the monitor was down refresh immediately
        # instead of showing "unreachable" until the next real transition.
        self._first_cycle = True
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

        # Agent HOSTS this collector is responsible for get the SAME ping/RTT
        # probe as network devices — against the agent's REAL IP (Agent.last_ip),
        # not the synthetic Device record that #133/#136 excluded. Same
        # device_reachability schema (so the Servers UI reads it by device_id),
        # but a distinct "Host unreachable" alert (see _apply_agents).
        await self._cycle_agents(timeout)

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
        # First cycle after startup: push current state for every device that
        # didn't already transition, so stale UIs (showing a pre-restart
        # last_seen) refresh without waiting for a status change.
        if self._first_cycle:
            self._first_cycle = False
            transitioned = {device_id for _, _, device_id, _ in transitions}
            for d, ok, _method, _rtt in results:
                if d["id"] in transitioned:
                    continue
                await self._push_ws({
                    "device_id": d["id"], "hostname": d.get("hostname"),
                    "is_reachable": ok,
                    "status": "active" if ok else d.get("status", "unreachable"),
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

    # ── agent hosts (ping/RTT for servers) ────────────────────────────────────

    async def _cycle_agents(self, timeout: float):
        """Probe the agent hosts this collector owns, store RTT, fire/resolve the
        Host-unreachable alert. Mirrors the device path but against Agent.last_ip
        and with the agent-specific apply logic."""
        from asgiref.sync import sync_to_async
        agents = await sync_to_async(self._fetch_agent_targets)()
        if not agents:
            return
        results = await asyncio.gather(*[self._check(a, timeout) for a in agents])
        # SAME device_reachability schema, keyed by the agent's device_id, so the
        # Servers list/detail read ping/RTT exactly like the Devices list does.
        self._write_reachability(results)
        await sync_to_async(self._apply_agents)(results)
        reachable = sum(1 for _a, ok, _m, _r in results if ok)
        logger.info("reachability: %d/%d agent hosts reachable", reachable, len(results))

    @staticmethod
    def _fetch_agent_targets() -> list[dict]:
        """Agent hosts this (the default/local) collector is responsible for, as
        probe targets against the agent's REAL IP (Agent.last_ip).

        Target set = agents whose effective collector is the default collector
        (or unresolved → the default is the fallback) AND whose last_ip is
        routable. An agent with no usable IP is OMITTED (reported "not probed" by
        the API) — never a false "unreachable" (#133 lesson). Stage 1 origination
        is central = the is_default collector; Stage 2 moves it on-site via NATS
        with the same data/UI."""
        from apps.agents.models import Agent
        from apps.collectors.models import Collector

        default = Collector.objects.filter(is_default=True).first()
        default_pk = default.pk if default else None
        targets = []
        agents = (Agent.objects.filter(status=Agent.Status.ACTIVE)
                  .select_related("device", "device__site", "device__site__default_collector",
                                  "device__collector"))
        for a in agents:
            if not is_pingable_ip(a.last_ip):
                continue  # not probeable → no false unreachable
            # Resolve which collector owns this agent host (mirrors
            # collectors.resolve.effective_collector precedence, inlined to avoid a
            # per-agent default-collector query). With no remote collectors yet
            # everything falls back to the default, so central probes it all.
            dev = a.device if a.device_id else None
            if dev is not None and dev.collector_id:
                owner_pk = dev.collector_id
            elif dev is not None and dev.site_id and dev.site.default_collector_id:
                owner_pk = dev.site.default_collector_id
            else:
                owner_pk = default_pk
            if default_pk is not None and owner_pk != default_pk:
                continue  # owned by a remote collector → it will probe (Stage 2)
            targets.append({
                "id": a.device_id or a.id,      # device_id tags the RTT for the UI
                "hostname": a.hostname,
                "management_ip": a.last_ip, "ip_address": a.last_ip,
                "status": "active", "consecutive_failures": 0,
                "_agent_id": str(a.id), "_is_agent": True,
            })
        return targets

    def _apply_agents(self, results) -> None:
        """Fire/resolve the standing **Host Unreachable** AlertEvent per agent host
        (distinct from agent_offline — that's the agent's self-report; this is the
        collector's network probe). DB-direct debounce like liveness/stability;
        `labels.device_id` + `labels.agent_id` linkage so it surfaces on the
        server. In-memory failure counting gates FAILURE_THRESHOLD."""
        from django.utils import timezone
        from apps.alerts.models import AlertEvent, AlertRule
        from apps.alerting.maintenance import is_in_maintenance

        now = timezone.now()
        for d, ok, _method, _rtt in results:
            aid = d["_agent_id"]
            did = d["id"]
            host = d["hostname"]
            open_ev = AlertEvent.objects.filter(
                state=AlertEvent.State.FIRING,
                labels__alert_type="host_unreachable",
                labels__agent_id=aid,
            ).first()
            if ok:
                self._agent_fails.pop(aid, None)
                if open_ev is not None:
                    open_ev.state = AlertEvent.State.RESOLVED
                    open_ev.resolved_at = now
                    open_ev.resolution_note = "Host reachable again."
                    open_ev.save(update_fields=["state", "resolved_at", "resolution_note"])
                continue
            fails = self._agent_fails.get(aid, 0) + 1
            self._agent_fails[aid] = fails
            if fails >= FAILURE_THRESHOLD and open_ev is None:
                if is_in_maintenance(device_id=did, severity="high"):
                    continue
                rule, _ = AlertRule.objects.get_or_create(
                    name=HOST_UNREACHABLE_RULE,
                    defaults={"description": ("A monitored server's host is not "
                                              "reachable over the network from its collector."),
                              "severity": AlertRule.Severity.HIGH,
                              "condition": {"rule_type": "host_unreachable"},
                              "cooldown_minutes": 0, "is_system": True},
                )
                AlertEvent.objects.create(
                    rule=rule, state=AlertEvent.State.FIRING,
                    labels={"source": "reachability_monitor", "alert_type": "host_unreachable",
                            "device_id": did, "agent_id": aid, "hostname": host,
                            "severity": "critical"},
                    annotations={
                        "title": f"Host unreachable: {host}",
                        "message": (f"The collector cannot reach {host} ({d['ip_address']}) "
                                    f"over the network ({fails} consecutive failures). The agent "
                                    f"may still be reporting (host up, network path down) or the "
                                    f"host may be down."),
                        "severity": "critical"},
                )

    # ── data access (sync) ────────────────────────────────────────────────────

    @staticmethod
    def _fetch_devices() -> list[dict]:
        from apps.devices.models import Device
        # Exclude AGENT-LINKED devices: agent-backed servers report their own
        # liveness (agent check-in + the agent-offline watchdog), and their
        # Device IP is frequently synthetic (loopback) — the central TCP probe
        # would only produce a permanent false "unreachable". Network
        # reachability for agent HOSTS comes from the collector pinging the
        # agent's REAL IP (ping/RTT), not this device-IP probe.
        rows = list(
            Device.objects.filter(status__in=[Device.Status.ACTIVE, Device.Status.UNREACHABLE])
            .filter(agent__isnull=True)
            .values("id", "hostname", "management_ip", "ip_address", "status", "consecutive_failures")
        )
        # Belt-and-suspenders: also drop any device whose effective probe host is
        # loopback/synthetic (covers a synthetic IP even if the agent link is
        # gone), matching _check's `management_ip or ip_address` precedence.
        out = []
        for d in rows:
            host = (d.get("management_ip") or d.get("ip_address") or "").strip().lower()
            if host in SYNTHETIC_HOSTS:
                continue
            out.append(d)
        return out

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
        """
        TCP-connect to each REACHABILITY_PORT in order (SSH first, then HTTPS).
        Firewalls (e.g. FortiOS trusted-hosts) often block SSH from the collector
        but answer on 443, so the 443 fallback keeps such devices marked live.
        Returns (device, reachable_bool, method="tcp/<port>", rtt_ms).
        """
        host = d.get("management_ip") or d.get("ip_address")
        if not host:
            return d, False, "tcp", None
        for port in REACHABILITY_PORTS:
            start = time.monotonic()
            try:
                fut = asyncio.open_connection(host, port)
                _reader, writer = await asyncio.wait_for(fut, timeout=timeout)
                rtt_ms = (time.monotonic() - start) * 1000
                writer.close()
                try:
                    await writer.wait_closed()
                except Exception:
                    pass
                return d, True, f"tcp/{port}", rtt_ms
            except Exception:
                continue
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
