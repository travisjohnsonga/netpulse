"""
Collect ARP + MAC tables from devices over SSH and persist them.

Default cadence (scheduler): every 6 hours. ARP changes often, MAC less so —
6h is a sensible default; can be triggered on demand via --device-id or the
device "Collect Now" API action.
"""
from __future__ import annotations

import logging

from django.core.management.base import BaseCommand

from apps.compliance.collector import get_credentials
from apps.devices.models import Device
from apps.arp_mac.collector import DEVICE_TYPE_MAP, collect_arp_mac, store_arp_mac

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Collect ARP and MAC address tables from devices over SSH."

    def add_arguments(self, parser):
        parser.add_argument("--device-id", type=int, help="Collect from a single device")
        parser.add_argument("--all", action="store_true", help="Collect from all active devices")

    def handle(self, *args, **options):
        qs = Device.objects.filter(
            status=Device.Status.ACTIVE, platform__in=list(DEVICE_TYPE_MAP.keys()),
        ).select_related("credential_profile")
        if options.get("device_id"):
            qs = qs.filter(id=options["device_id"])

        devices = list(qs)
        if not devices:
            self.stdout.write("No matching active devices to collect.")
            return

        for device in devices:
            profile = device.credential_profile
            username = profile.ssh_username if profile else ""
            secrets = get_credentials(device)
            if not username or not secrets.get("ssh_password"):
                self.stdout.write(f"{device.hostname}: no SSH credentials — skipping")
                continue
            try:
                arp, mac = collect_arp_mac(device, secrets, username)
                n_arp, n_mac = store_arp_mac(device, arp, mac)
                self.stdout.write(self.style.SUCCESS(
                    f"{device.hostname}: {n_arp} ARP, {n_mac} MAC entries"))
            except Exception as exc:  # one device must not abort the run
                logger.exception("arp_mac collection failed for %s", device.hostname)
                self.stdout.write(self.style.ERROR(f"{device.hostname}: {exc}"))
