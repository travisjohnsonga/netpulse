"""
Populate the MACVendor prefix → vendor table.

Offline-first: with no arguments this loads a compact OUI snapshot bundled in the
repo (``apps/arp_mac/data/oui.csv.gz``), so vendor resolution works on
air-gapped installs with no internet access.

  * (no args)   load the bundled snapshot (instant, offline)
  * --refresh   pull the live IEEE registries (MA-L, MA-M, MA-S and IAB from
                https://standards-oui.ieee.org/) and FALL BACK to the bundled
                snapshot on any failure — a failed download never wipes or
                skips the table
  * --file PATH read an explicit local CSV (``.gz`` accepted)
  * --url  URL  override the MA-L registry URL used by --refresh

The refresh downloads the WHOLE public registry; nothing about the local
network (no MAC, no OUI) is sent anywhere. Lookups then run against the table.

Both CSV header styles are accepted: the compact bundled form
(``oui,organization`` with ``oui`` as ``aa:bb:cc``, ``aa:bb:cc:d`` or
``aa:bb:cc:dd:e``) and the IEEE form (``Registry,Assignment,Organization
Name,Address`` with ``Assignment`` 6, 7 or 9 hex digits). Any source may be
gzip-compressed.
"""
from __future__ import annotations

import csv
import gzip
import io
import logging
import os
import re

from django.core.management.base import BaseCommand

from apps.arp_mac.normalize import prefix_from_assignment

logger = logging.getLogger(__name__)

IEEE_OUI_CSV = "https://standards-oui.ieee.org/oui/oui.csv"

# (name, url, stored prefix length). MA-L is required; the smaller blocks are
# best-effort and fall back to their bundled entries individually.
IEEE_EXTRA_REGISTRIES = (
    ("MA-M", "https://standards-oui.ieee.org/oui28/mam.csv", 10),
    ("MA-S", "https://standards-oui.ieee.org/oui36/oui36.csv", 13),
    ("IAB", "https://standards-oui.ieee.org/iab/iab.csv", 13),
)

# The IEEE site rejects the default python-requests User-Agent (HTTP 418
# "Request Rejected"), which silently pinned every install to the bundled
# snapshot. Any honest product UA is accepted.
_HTTP_HEADERS = {"User-Agent": "spane-oui-update/1.0 (+https://spane.app)"}

# Bundled snapshot: apps/arp_mac/data/oui.csv.gz (this file lives at
# apps/arp_mac/management/commands/update_mac_vendors.py → up three levels).
BUNDLED_OUI_GZ = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data", "oui.csv.gz",
)

_COMPACT_OUI = re.compile(r"^[0-9a-f]{2}:[0-9a-f]{2}:[0-9a-f]{2}(:[0-9a-f]|:[0-9a-f]{2}:[0-9a-f])?$")


def _decode(raw: bytes) -> str:
    """Decode CSV bytes to text, transparently gunzipping a gzip payload."""
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    return raw.decode("utf-8", errors="replace")


def _parse_oui_csv(text: str) -> dict:
    """Parse either header style into ``{aa:bb:cc: vendor}``."""
    reader = csv.DictReader(io.StringIO(text))
    fields = reader.fieldnames or []
    compact = "oui" in fields
    rows: dict[str, str] = {}
    for row in reader:
        if compact:
            oui = (row.get("oui") or "").strip().lower()
            vendor = (row.get("organization") or "").strip()
            if _COMPACT_OUI.match(oui) and vendor:
                rows[oui] = vendor[:128]
        else:
            oui = prefix_from_assignment(row.get("Assignment") or "")
            vendor = (row.get("Organization Name") or "").strip()
            if oui and vendor:
                rows[oui] = vendor[:128]
    return rows


def _load_bundled() -> dict:
    with open(BUNDLED_OUI_GZ, "rb") as fh:
        return _parse_oui_csv(_decode(fh.read()))


class Command(BaseCommand):
    help = "Populate the MACVendor prefix → vendor table (offline-first; --refresh pulls live IEEE)."

    def add_arguments(self, parser):
        parser.add_argument("--refresh", action="store_true",
                            help="Pull the live IEEE registries (falls back to the bundled snapshot on failure)")
        parser.add_argument("--url", default=IEEE_OUI_CSV, help="MA-L registry URL used by --refresh")
        parser.add_argument("--file", help="Read from a local CSV (.gz accepted) instead")

    def handle(self, *args, **options):
        from apps.arp_mac.models import MACVendor

        file_path = options.get("file")
        refresh = options.get("refresh")

        if file_path:
            rows = self._from_file(file_path)
        elif refresh:
            rows = self._from_refresh(options["url"])
        else:
            rows = _load_bundled()
            self.stdout.write(f"Loaded bundled registry snapshot ({len(rows)} entries).")

        if not rows:
            # Never wipe the table on an empty/failed parse.
            self.stderr.write(self.style.ERROR("No OUI rows parsed — aborting (table unchanged)."))
            return

        objs = [MACVendor(oui=oui, vendor=vendor) for oui, vendor in rows.items()]
        MACVendor.objects.all().delete()
        MACVendor.objects.bulk_create(objs, batch_size=2000)
        self.stdout.write(self.style.SUCCESS(f"Loaded {len(objs)} vendor prefix entries."))

    def _from_file(self, path: str) -> dict:
        with open(path, "rb") as fh:
            return _parse_oui_csv(_decode(fh.read()))

    def _download(self, url: str) -> dict:
        import requests
        self.stdout.write(f"Downloading {url} …")
        resp = requests.get(url, timeout=60, headers=_HTTP_HEADERS)
        resp.raise_for_status()
        rows = _parse_oui_csv(_decode(resp.content))
        if not rows:
            raise ValueError("no rows parsed from download")
        return rows

    def _from_refresh(self, url: str) -> dict:
        """Download the live registries; fall back to the bundled snapshot on failure.

        MA-L failing means the whole bundled snapshot is used. A smaller
        registry failing keeps the bundled entries of its prefix length, so one
        flaky download never drops vendors that resolved before.
        """
        try:
            rows = self._download(url)
            self.stdout.write(f"Downloaded {len(rows)} MA-L entries from IEEE.")
        except Exception as exc:
            self.stderr.write(self.style.WARNING(
                f"OUI download failed ({exc}); falling back to the bundled snapshot."))
            logger.warning("update_mac_vendors: live refresh failed, using bundled snapshot: %s", exc)
            return _load_bundled()

        failed_lengths: set[int] = set()
        for name, extra_url, length in IEEE_EXTRA_REGISTRIES:
            try:
                extra = self._download(extra_url)
                rows.update(extra)
                self.stdout.write(f"Downloaded {len(extra)} {name} entries from IEEE.")
            except Exception as exc:
                failed_lengths.add(length)
                self.stderr.write(self.style.WARNING(f"{name} download failed ({exc}); using bundled entries."))
                logger.warning("update_mac_vendors: %s refresh failed, using bundled entries: %s", name, exc)
        if failed_lengths:
            for oui, vendor in _load_bundled().items():
                if len(oui) in failed_lengths:
                    rows.setdefault(oui, vendor)
        return rows
