from django.core.management.base import BaseCommand

# Example compliance templates seeded DISABLED by default — starting points an
# admin reviews, customises, and enables. Idempotent: matched by name, never
# overwrites an existing template.
EXAMPLE_TEMPLATES = [
    {
        "name": "NTP Policy",
        "description": "Require the standard NTP servers (all platforms).",
        "platform": "",  # global
        "template_content": "ntp server {{ ntp_server_1 }}\nntp server {{ ntp_server_2 }}\n",
        "variables": {
            "ntp_server_1": "10.0.0.1",
            "ntp_server_2": "10.0.0.2",
        },
    },
    {
        "name": "AOS-CX Access Switch Baseline",
        "description": "Baseline NTP / logging / SNMP / banner for AOS-CX access switches.",
        "platform": "aos_cx",
        "template_content": (
            "ntp server {{ ntp_server_1 }}\n"
            "ntp server {{ ntp_server_2 }}\n"
            "logging {{ syslog_server }}\n"
            "snmp-server community {{ snmp_community }} operator\n"
            "banner motd {{ banner_text }}\n"
        ),
        "variables": {
            "ntp_server_1": "10.0.0.1",
            "ntp_server_2": "10.0.0.2",
            "syslog_server": "10.0.0.5",
            "snmp_community": "public",
            "banner_text": "Authorized access only",
        },
    },
]


class Command(BaseCommand):
    help = "Seed example compliance templates (disabled by default) — idempotent, non-destructive."

    def handle(self, *args, **options):
        from apps.compliance.models import ComplianceTemplate

        created = []
        for spec in EXAMPLE_TEMPLATES:
            if ComplianceTemplate.objects.filter(name=spec["name"]).exists():
                continue
            ComplianceTemplate.objects.create(
                name=spec["name"],
                description=spec["description"],
                platform=spec["platform"],
                template_content=spec["template_content"],
                variables=spec["variables"],
                enabled=False,  # examples ship disabled — admin reviews + enables
            )
            created.append(spec["name"])

        if created:
            self.stdout.write(self.style.SUCCESS(
                f"Created {len(created)} example compliance template(s) (disabled): "
                + ", ".join(created)))
        else:
            self.stdout.write("Example compliance templates already seeded — nothing to do.")
