from django.core.management.base import BaseCommand

# Default device roles seeded on a fresh install. Idempotent and non-destructive:
# existing roles (matched by slug) keep any colour/name the admin has customised.
DEFAULT_ROLES = [
    {"name": "Core Switch",         "slug": "core-switch",   "color": "#3b82f6"},  # blue
    {"name": "Distribution Switch", "slug": "dist-switch",   "color": "#8b5cf6"},  # purple
    {"name": "Access Switch",       "slug": "access-switch", "color": "#10b981"},  # green
    {"name": "Firewall",            "slug": "firewall",      "color": "#ef4444"},  # red
    {"name": "Router",              "slug": "router",        "color": "#f59e0b"},  # amber
    {"name": "Wireless AP",         "slug": "wireless-ap",   "color": "#06b6d4"},  # cyan
    {"name": "Server",              "slug": "server",        "color": "#64748b"},  # slate
    {"name": "OOB",                 "slug": "oob",           "color": "#84cc16"},  # lime
]


class Command(BaseCommand):
    help = "Seed the default device roles — idempotent, non-destructive."

    def handle(self, *args, **options):
        from apps.devices.models import DeviceRole

        created = []
        for spec in DEFAULT_ROLES:
            _, was_created = DeviceRole.objects.get_or_create(
                slug=spec["slug"],
                defaults={"name": spec["name"], "color": spec["color"]},
            )
            if was_created:
                created.append(spec["name"])

        if created:
            self.stdout.write(self.style.SUCCESS(f"Created roles: {', '.join(created)}"))
        else:
            self.stdout.write("Device roles already seeded — nothing to do.")
