"""Parse and validate a MIB file: object count + unresolved-symbol warnings."""
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.mibs import index


class Command(BaseCommand):
    help = "Validate a MIB file's syntax and report object count + warnings."

    def add_arguments(self, parser):
        parser.add_argument("file", help="Path to the MIB file (.my/.mib/.txt).")

    def handle(self, *args, **options):
        path = Path(options["file"])
        if not path.is_file():
            raise CommandError(f"file not found: {path}")
        result = index.validate_text(path.read_text(errors="replace"))
        self.stdout.write(f"Module:  {result['module'] or '(unnamed)'}")
        self.stdout.write(f"Objects: {result['objects']}")
        if result["warnings"]:
            for w in result["warnings"]:
                self.stdout.write(self.style.WARNING(f"  ⚠ {w}"))
        if result["ok"]:
            self.stdout.write(self.style.SUCCESS("OK — MIB parsed."))
        else:
            raise CommandError("no object definitions found — not a valid MIB?")
