"""List all loaded MIBs and their object counts."""
from django.core.management.base import BaseCommand

from apps.mibs import index


class Command(BaseCommand):
    help = "List all loaded MIBs (name, path, object count)."

    def handle(self, *args, **options):
        mibs = index.list_mibs()
        if not mibs:
            self.stdout.write("No MIBs found under MIBS_DIR.")
            return
        width = max(len(m["name"]) for m in mibs)
        for m in sorted(mibs, key=lambda x: (x["path"], x["name"])):
            self.stdout.write(
                f"{m['name']:<{width}}  {m['path'] or '.':<18}  {m['objects']:>4} objects")
        self.stdout.write(self.style.SUCCESS(f"\n{len(mibs)} MIB(s) loaded."))
