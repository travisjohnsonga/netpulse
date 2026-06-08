"""collect_lldp — refresh LLDP neighbors across the fleet, now.

Runs the same per-device LLDP discovery the scheduler runs every 30 minutes
(LLDP_COLLECT_INTERVAL_S), on demand. Use it to populate the LLDPNeighbor table
without waiting for the next scheduler tick (e.g. right after the table was
first added). Persistence reuses apps.devices.topology.discover_links.
"""
from django.core.management.base import BaseCommand

from apps.devices.models import Device
from apps.devices.topology import collect_all_lldp


class Command(BaseCommand):
    help = "Collect LLDP neighbors from reachable active devices and persist them."

    def add_arguments(self, parser):
        parser.add_argument("--device", type=int, help="Limit to a single device id.")
        parser.add_argument("--all", action="store_true",
                            help="Include unreachable/inactive devices too.")

    def handle(self, *args, **options):
        if options.get("device"):
            devices = Device.objects.filter(id=options["device"])
        elif options.get("all"):
            devices = Device.objects.all()
        else:
            devices = None  # default: reachable active devices
        summary = collect_all_lldp(devices)
        self.stdout.write(self.style.SUCCESS(
            f"LLDP collection complete: {summary['devices']} device(s), "
            f"{summary['neighbors']} neighbor(s), {summary['failed']} failed"))
