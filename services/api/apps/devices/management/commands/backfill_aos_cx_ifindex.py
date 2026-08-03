"""backfill_aos_cx_ifindex — resolve numeric SNMP ifIndex for AOS-CX interfaces.

AOS-CX interface discovery is REST-based (interfaces are identified by NAME,
e.g. "1/1/8", not a numeric SNMP ifIndex). Before the fix in
``apps.telemetry.discovery._discover_via_aos_cx_rest`` every discovered
interface was persisted with ``if_index=None``, and
``apps.devices.snmp_publish.build_device_payload`` silently skips per-interface
traffic/status/error OIDs for any interface with no index — so AOS-CX devices
never got interface-level polling.

The code fix only affects NEW discovery runs; existing ``MonitoredInterface``
rows keep ``if_index=None`` and won't self-heal. This one-time backfill re-runs
interface discovery for AOS-CX devices (which now resolves the index via a
lightweight IF-MIB SNMP walk), updates the ``if_index`` on existing rows by
matching on ``if_name``, and republishes each device's config to the poller so
interface OIDs start being requested immediately.

Idempotent: safe to re-run. Interfaces whose name can't be matched to an SNMP
index keep ``if_index=None`` (graceful per-interface degradation).

    docker compose exec api python manage.py backfill_aos_cx_ifindex
    docker compose exec api python manage.py backfill_aos_cx_ifindex --device 18
    docker compose exec api python manage.py backfill_aos_cx_ifindex --dry-run
"""
from django.core.management.base import BaseCommand

from apps.devices.models import Device
from apps.telemetry import discovery
from apps.telemetry.discovery import _iface_match_key
from apps.telemetry.models import MonitoredInterface


class Command(BaseCommand):
    help = "Backfill numeric SNMP ifIndex on AOS-CX MonitoredInterface rows."

    def add_arguments(self, parser):
        parser.add_argument("--device", type=int,
                            help="Limit to a single device id (default: all aos_cx).")
        parser.add_argument("--dry-run", action="store_true",
                            help="Report what would change without writing.")

    def handle(self, *args, **options):
        qs = Device.objects.filter(platform="aos_cx")
        if options.get("device"):
            qs = qs.filter(id=options["device"])

        dry = options.get("dry_run")
        total_dev = total_updated = total_unmatched = total_failed = 0

        for device in qs:
            total_dev += 1
            try:
                rows = discovery.discover_interfaces(device)
            except Exception as exc:  # noqa: BLE001 — one bad device never aborts the batch
                total_failed += 1
                self.stderr.write(self.style.WARNING(
                    f"[{device.id}] {device.hostname}: discovery failed: {exc}"))
                continue

            # Resolved index by normalised interface name.
            idx_by_key = {
                _iface_match_key(r.get("if_name") or ""): r.get("if_index")
                for r in rows if r.get("if_index") is not None
            }

            updated = unmatched = 0
            for mi in MonitoredInterface.objects.filter(device=device):
                new_idx = idx_by_key.get(_iface_match_key(mi.if_name))
                if new_idx is None:
                    if mi.if_index is None:
                        unmatched += 1
                    continue
                if mi.if_index != new_idx:
                    updated += 1
                    if not dry:
                        mi.if_index = new_idx
                        mi.save(update_fields=["if_index"])

            total_updated += updated
            total_unmatched += unmatched

            # Republish so the poller picks up the new interface OIDs now.
            if updated and not dry:
                try:
                    from apps.devices.snmp_publish import publish_device_upsert
                    publish_device_upsert(device)
                except Exception as exc:  # noqa: BLE001 — publish is best-effort
                    self.stderr.write(self.style.WARNING(
                        f"[{device.id}] {device.hostname}: republish failed: {exc}"))

            self.stdout.write(
                f"[{device.id}] {device.hostname}: "
                f"{updated} updated, {unmatched} still-unmatched"
                + (" (dry-run)" if dry else ""))

        verb = "would update" if dry else "updated"
        self.stdout.write(self.style.SUCCESS(
            f"AOS-CX ifIndex backfill: {total_dev} device(s), "
            f"{verb} {total_updated} interface(s), "
            f"{total_unmatched} unmatched, {total_failed} device(s) failed"))
