from django.core.management.base import BaseCommand

# Example hostname rules seeded DISABLED by default — they are starting points an
# admin can enable/edit (the wco2 lab naming convention). Idempotent: matched by
# name, never overwrites an existing rule. Role/site FKs are resolved by slug/name
# at seed time and silently skipped if the target doesn't exist.
EXAMPLE_RULES = [
    # Site rule — match the site-code prefix.
    {"name": "WCO2 Site", "pattern": r"^wco2-", "rule_type": "site",
     "site_name": "WCO2", "priority": 10},
    # Role rules — match the device-type code in the hostname.
    {"name": "Core/Distribution switches (crt/mdf/ddf)", "pattern": r"-(crt|mdf|ddf)-",
     "rule_type": "role", "role_slug": "core-switch", "priority": 20},
    {"name": "IDF/Access switches", "pattern": r"-(idf|asw|acc)-",
     "rule_type": "role", "role_slug": "access-switch", "priority": 20},
    {"name": "Firewalls (fw/fwl/pfw)", "pattern": r"-(fw|fwl|pfw|firewall)-",
     "rule_type": "role", "role_slug": "firewall", "priority": 20},
    {"name": "Routers (rtr/router/gw)", "pattern": r"-(rtr|router|gw|rt)-",
     "rule_type": "role", "role_slug": "router", "priority": 20},
    {"name": "Wireless APs (ap/wap/wifi)", "pattern": r"-(ap|wap|wifi|wireless)-",
     "rule_type": "role", "role_slug": "wireless-ap", "priority": 20},
]


class Command(BaseCommand):
    help = "Seed example hostname rules (disabled by default) — idempotent, non-destructive."

    def handle(self, *args, **options):
        from apps.devices.models import DeviceRole, HostnameRule, Site

        created = []
        for spec in EXAMPLE_RULES:
            if HostnameRule.objects.filter(name=spec["name"]).exists():
                continue
            role = None
            if spec.get("role_slug"):
                role = DeviceRole.objects.filter(slug=spec["role_slug"]).first()
            site = None
            if spec.get("site_name"):
                site = Site.objects.filter(name=spec["site_name"]).first()
            HostnameRule.objects.create(
                name=spec["name"],
                pattern=spec["pattern"],
                rule_type=spec["rule_type"],
                role=role,
                site=site,
                priority=spec["priority"],
                enabled=False,  # examples ship disabled — admin reviews + enables
            )
            created.append(spec["name"])

        if created:
            self.stdout.write(self.style.SUCCESS(
                f"Created {len(created)} example hostname rule(s) (disabled): "
                + ", ".join(created)))
        else:
            self.stdout.write("Example hostname rules already seeded — nothing to do.")
