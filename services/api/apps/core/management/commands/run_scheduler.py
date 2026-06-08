"""
run_scheduler — the authoritative periodic-task scheduler.

This is the single scheduling system for NetPulse (the management-command-loop
pattern used by run_config_manager / reachability-monitor / check-engine).
Celery is NOT used for periodic work — no beat schedule or tasks are defined.

Startup (run once on boot, best-effort/idempotent):
  - seed default/system alert rules (incl. the temperature rules)
  - ensure OpenBao is unsealed + the token is readable (so credential reads work)
  - populate the MAC-vendor OUI table if it is empty

Periodic (each on its own cadence; the loop wakes every --tick seconds):
  - resolved-alert purge      — daily   (runs on startup)
  - ARP/MAC table collection  — every 6h (ARP_MAC_COLLECT_INTERVAL_S)
  - MAC-vendor OUI refresh     — weekly  (MAC_VENDOR_UPDATE_INTERVAL_S)

The 6h/weekly tasks first fire one interval after startup so a restart doesn't
stampede the fleet (SSH) or re-download the OUI registry every boot.
"""
import logging
import os
import signal
import time

from django.core.management import call_command
from django.core.management.base import BaseCommand

logger = logging.getLogger(__name__)

ALERT_RETENTION_DAYS = 90
ALERT_PURGE_INTERVAL_S = 24 * 3600                                  # daily
ARP_MAC_INTERVAL_S = int(os.environ.get("ARP_MAC_COLLECT_INTERVAL_S", str(6 * 3600)))
MAC_VENDOR_INTERVAL_S = int(os.environ.get("MAC_VENDOR_UPDATE_INTERVAL_S", str(7 * 24 * 3600)))
HOSTNAME_CHECK_INTERVAL_S = int(os.environ.get("HOSTNAME_CHECK_INTERVAL_S", str(24 * 3600)))
UNIFI_SYNC_INTERVAL_S = int(os.environ.get("UNIFI_SYNC_INTERVAL_S", str(6 * 3600)))
OS_PLATFORM_REFRESH_INTERVAL_S = int(os.environ.get("OS_PLATFORM_REFRESH_INTERVAL_S", str(6 * 3600)))
OS_VERSION_SEED_INTERVAL_S = int(os.environ.get("OS_VERSION_SEED_INTERVAL_S", str(24 * 3600)))
# Heartbeat the local collector frequently; with the default 300s tick it
# effectively fires every tick (well under the 600s health window).
COLLECTOR_HEARTBEAT_INTERVAL_S = int(os.environ.get("COLLECTOR_HEARTBEAT_INTERVAL_S", "60"))
DEFAULT_TICK_S = 300


class Command(BaseCommand):
    help = "Run periodic maintenance tasks (alert purge, ARP/MAC collection, OUI refresh)."

    def add_arguments(self, parser):
        # --interval kept for backwards compatibility (alert-purge cadence).
        parser.add_argument("--interval", type=int, default=ALERT_PURGE_INTERVAL_S)
        parser.add_argument("--tick", type=int, default=DEFAULT_TICK_S)
        parser.add_argument("--once", action="store_true")

    def handle(self, *args, **options):
        stop = {"flag": False}

        def _shutdown(*_):
            stop["flag"] = True
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                signal.signal(sig, _shutdown)
            except ValueError:
                pass

        self._run_startup_tasks()

        # (name, interval_s, fn, run_on_start) — last_run filled below.
        tasks = [
            ["alert_purge", options["interval"], self._purge_alerts, True, None],
            ["arp_mac", ARP_MAC_INTERVAL_S, self._collect_arp_mac, False, None],
            ["mac_vendors", MAC_VENDOR_INTERVAL_S, self._update_mac_vendors, False, None],
            ["hostname_check", HOSTNAME_CHECK_INTERVAL_S, self._check_hostnames, False, None],
            ["unifi_sync", UNIFI_SYNC_INTERVAL_S, self._sync_unifi, False, None],
            ["os_platform_refresh", OS_PLATFORM_REFRESH_INTERVAL_S, self._refresh_os_platforms, False, None],
            ["os_version_seed", OS_VERSION_SEED_INTERVAL_S, self._seed_os_versions, False, None],
            ["collector_heartbeat", COLLECTOR_HEARTBEAT_INTERVAL_S, self._collector_heartbeat, True, None],
        ]
        now = time.monotonic()
        for t in tasks:
            t[4] = None if t[3] else now   # run_on_start=False → first run one interval out

        tick = max(5, options["tick"])
        logger.info("scheduler started (tick=%ss; alert_purge=%ss, arp_mac=%ss, mac_vendors=%ss)",
                    tick, options["interval"], ARP_MAC_INTERVAL_S, MAC_VENDOR_INTERVAL_S)
        while not stop["flag"]:
            now = time.monotonic()
            for t in tasks:
                name, interval, fn, _ros, last = t
                if last is None or (now - last) >= interval:
                    try:
                        fn()
                    except Exception as exc:
                        logger.error("scheduler: task %s failed: %s", name, exc)
                    t[4] = now
            if options["once"]:
                return
            slept = 0
            while slept < tick and not stop["flag"]:
                time.sleep(min(5, tick - slept))
                slept += 5
        logger.info("scheduler stopped")

    # ── startup one-shots (best-effort; never block the loop) ────────────────
    def _run_startup_tasks(self):
        for name, fn in (("seed_alert_rules", self._seed_alert_rules),
                         ("openbao_refresh", self._openbao_refresh),
                         ("register_local_collector", self._register_local_collector),
                         ("seed_mac_vendors", self._seed_mac_vendors_if_empty)):
            try:
                fn()
            except Exception as exc:
                logger.error("scheduler: startup task %s failed: %s", name, exc)

    def _seed_alert_rules(self):
        call_command("seed_alert_rules")

    def _register_local_collector(self):
        from apps.collectors.management.commands.register_local_collector import (
            register_local_collector,
        )
        register_local_collector()

    def _openbao_refresh(self):
        # Idempotent: unseals if sealed, refreshes the readable token; no-op if
        # already unsealed. Lets the scheduler read SSH creds for ARP/MAC.
        call_command("init_openbao")

    def _seed_mac_vendors_if_empty(self):
        from apps.arp_mac.models import MACVendor
        if not MACVendor.objects.exists():
            logger.info("scheduler: MAC-vendor table empty — loading OUI registry")
            call_command("update_mac_vendors")

    # ── periodic tasks ───────────────────────────────────────────────────────
    def _purge_alerts(self):
        from apps.alerts.management.commands.purge_resolved_alerts import purge_resolved_alerts
        n = purge_resolved_alerts(ALERT_RETENTION_DAYS)
        if n:
            logger.info("scheduler: purged %d resolved alerts (>%dd)", n, ALERT_RETENTION_DAYS)

    def _collect_arp_mac(self):
        logger.info("scheduler: collecting ARP/MAC tables")
        call_command("collect_arp_mac", all=True)

    def _update_mac_vendors(self):
        logger.info("scheduler: refreshing MAC-vendor OUI registry")
        call_command("update_mac_vendors")

    def _check_hostnames(self):
        logger.info("scheduler: verifying device hostnames (SNMP sysName / DNS)")
        from apps.devices.hostname_check import check_all_hostnames
        check_all_hostnames()

    def _sync_unifi(self):
        logger.info("scheduler: syncing enabled UniFi controllers")
        from apps.integrations.unifi_sync import sync_all_controllers
        sync_all_controllers()

    def _refresh_os_platforms(self):
        logger.info("scheduler: refreshing OS-version fleet inventory")
        from apps.compliance.os_policy import refresh_discovered_platforms
        n = refresh_discovered_platforms()
        logger.info("scheduler: OS-version fleet inventory — %d combos tracked", n)

    def _seed_os_versions(self):
        from apps.compliance.os_policy import seed_os_versions_from_inventory
        r = seed_os_versions_from_inventory()
        if r["created"]:
            logger.info("scheduler: seeded %d new OS-version placeholder(s) from inventory", r["created"])

    def _collector_heartbeat(self):
        # Refresh the local collector's last_seen_at so its health reflects a
        # live engine fleet. Also (re)registers it if the row is missing.
        from apps.collectors.management.commands.register_local_collector import (
            register_local_collector,
        )
        register_local_collector()
