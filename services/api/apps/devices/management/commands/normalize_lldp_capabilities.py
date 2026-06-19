"""Re-normalize every stored LLDP-neighbour capability list to canonical tokens.

LLDP collection already normalizes capabilities on ingest (topology.discover_links
→ lldp.normalize_capabilities), and migration 0029 did a one-time backfill — so
this is a safety net: it re-folds any row whose capabilities drifted (e.g. an
older code path, a restored DB, or a newly-added alias like "wireless-ap"). Pure
re-normalization, idempotent. Run on startup (entrypoint, api service) and on
demand. See apps.compliance.interface_compliance, which ALSO normalizes both the
rule trigger value and stored caps at match time, so capability rules match
across every spelling regardless.
"""
from __future__ import annotations

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Re-normalize stored LLDP neighbour capabilities to canonical tokens."

    def handle(self, *args, **options):
        from apps.devices.lldp import normalize_capabilities
        from apps.devices.models import LLDPNeighbor

        updated = 0
        for nb in LLDPNeighbor.objects.only("id", "capabilities").iterator():
            current = nb.capabilities or []
            canonical = normalize_capabilities(current)
            if canonical != current:
                LLDPNeighbor.objects.filter(pk=nb.pk).update(capabilities=canonical)
                updated += 1
        self.stdout.write(f"normalized capabilities on {updated} LLDP neighbor(s)")
