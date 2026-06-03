"""
Populate the MACVendor OUI → vendor table from the IEEE registry.

Run on first setup and monthly thereafter. Source:
https://standards-oui.ieee.org/oui/oui.csv  (Registry,Assignment,Organization
Name,Address). Assignment is a 6-hex OUI (AABBCC) → stored as "aa:bb:cc".
"""
from __future__ import annotations

import csv
import io
import logging

from django.core.management.base import BaseCommand

logger = logging.getLogger(__name__)

IEEE_OUI_CSV = "https://standards-oui.ieee.org/oui/oui.csv"


class Command(BaseCommand):
    help = "Download the IEEE OUI registry and populate the MACVendor table."

    def add_arguments(self, parser):
        parser.add_argument("--url", default=IEEE_OUI_CSV, help="OUI CSV URL")
        parser.add_argument("--file", help="Read from a local CSV instead of downloading")

    def handle(self, *args, **options):
        from apps.arp_mac.models import MACVendor

        if options.get("file"):
            with open(options["file"], newline="", encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        else:
            import requests
            self.stdout.write(f"Downloading {options['url']} …")
            try:
                resp = requests.get(options["url"], timeout=60)
                resp.raise_for_status()
                text = resp.text
            except Exception as exc:
                self.stderr.write(self.style.ERROR(f"OUI download failed: {exc}"))
                return

        rows = {}
        reader = csv.DictReader(io.StringIO(text))
        for row in reader:
            assignment = (row.get("Assignment") or "").strip().lower()
            vendor = (row.get("Organization Name") or "").strip()
            if len(assignment) != 6 or not vendor:
                continue
            oui = ":".join(assignment[i:i + 2] for i in range(0, 6, 2))
            rows[oui] = vendor[:128]

        if not rows:
            self.stderr.write(self.style.ERROR("No OUI rows parsed — aborting (table unchanged)."))
            return

        objs = [MACVendor(oui=oui, vendor=vendor) for oui, vendor in rows.items()]
        MACVendor.objects.all().delete()
        MACVendor.objects.bulk_create(objs, batch_size=2000)
        self.stdout.write(self.style.SUCCESS(f"Loaded {len(objs)} OUI vendor entries."))
