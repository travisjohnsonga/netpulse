from django.core.management.base import BaseCommand

# Placeholder example sites for a fresh install — they give new users something
# to work with and let the example "Site N devices" hostname rules resolve to a
# real site. Seeded ONLY when no sites exist yet, so they never clutter a system
# that's already been configured. Rename/delete them and create your own.
EXAMPLE_SITES = [
    {"name": "Site 1", "description": "Example site — rename or delete and create your own sites."},
    {"name": "Site 2", "description": "Example site — rename or delete and create your own sites."},
]


class Command(BaseCommand):
    help = "Seed placeholder example sites (only when no sites exist yet) — idempotent, non-destructive."

    def handle(self, *args, **options):
        from apps.devices.models import Site

        if Site.objects.exists():
            self.stdout.write("Sites already exist — skipping example-site seed.")
            return

        created = []
        for spec in EXAMPLE_SITES:
            # Slug is auto-generated in Site.save(); name is unique.
            Site.objects.create(name=spec["name"], description=spec["description"])
            created.append(spec["name"])

        self.stdout.write(self.style.SUCCESS(
            f"Created {len(created)} example site(s): " + ", ".join(created)))
