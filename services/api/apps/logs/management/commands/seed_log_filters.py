from django.core.management.base import BaseCommand

# Example log filters seeded DISABLED by default — starting points an admin can
# review and enable. Idempotent: matched by name, never overwrites an existing
# filter.
EXAMPLE_FILTERS = [
    {
        "name": "Aruba Central Keepalives",
        "pattern": r"hpe-restd.*(AMM|UKWN)|tpmd.*TPM_Sign",
        "action": "suppress",
        "platforms": ["aos_cx"],
    },
    {
        "name": "SonicWall SSH Auth",
        "pattern": r"log-proxyd.*SSH session",
        "action": "tag",
        "tag": "ssh-auth",
        "platforms": ["sonicwall"],
    },
]


class Command(BaseCommand):
    help = "Seed example log filters (disabled by default) — idempotent, non-destructive."

    def handle(self, *args, **options):
        from apps.logs.models import LogFilter

        created = []
        for spec in EXAMPLE_FILTERS:
            if LogFilter.objects.filter(name=spec["name"]).exists():
                continue
            LogFilter.objects.create(
                name=spec["name"],
                pattern=spec["pattern"],
                action=spec["action"],
                tag=spec.get("tag", ""),
                color=spec.get("color", ""),
                platforms=spec.get("platforms", []),
                enabled=False,  # examples ship disabled — admin reviews + enables
            )
            created.append(spec["name"])

        if created:
            self.stdout.write(self.style.SUCCESS(
                f"Created {len(created)} example log filter(s) (disabled): "
                + ", ".join(created)))
        else:
            self.stdout.write("Example log filters already seeded — nothing to do.")
