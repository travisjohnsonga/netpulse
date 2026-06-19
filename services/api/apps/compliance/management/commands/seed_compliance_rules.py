"""
Seed example interface-compliance and role-consistency rules.

All seeded rules are DISABLED by default so they never penalise a fleet until an
admin reviews and enables them. Idempotent: matches by name, never overwrites a
rule an admin has since edited.
"""
from django.core.management.base import BaseCommand

# (name, defaults) — interface compliance rules, all disabled.
INTERFACE_RULES = [
    ("Wireless AP Port Config", {
        "description": ("Switch ports connected to wireless APs (detected via LLDP "
                        "capability wlan-access-point) should have edge/portfast STP "
                        "and not be trunked."),
        "trigger": "lldp_capability", "trigger_value": "wlan-access-point",
        "platform": "aos_cx",
        "checks": [
            {"type": "config_contains", "value": "spanning-tree",
             "description": "STP portfast/edge enabled", "severity": "warning"},
            {"type": "config_not_contains", "value": "trunk",
             "description": "Port not in trunk mode", "severity": "error"},
        ],
    }),
    ("AP PoE Priority Config", {
        "description": ("Wireless APs should have a PoE priority set so they stay "
                        "powered during a PoE budget event."),
        "trigger": "lldp_capability", "trigger_value": "wlan-access-point",
        "platform": "aos_cx",
        "checks": [
            {"type": "config_contains", "value": "priority",
             "description": "PoE priority configured", "severity": "info"},
        ],
    }),
    ("IP Phone Port Config", {
        "description": ("Switch ports connected to IP phones should have a voice "
                        "VLAN and QoS configured. Detected via LLDP telephone "
                        "capability."),
        "trigger": "lldp_capability", "trigger_value": "telephone", "platform": "",
        "checks": [
            {"type": "config_contains", "value": "voice",
             "description": "Voice VLAN configured", "severity": "warning"},
            {"type": "config_contains", "value": "spanning-tree",
             "description": "STP portfast enabled", "severity": "warning"},
            {"type": "config_contains", "value": "trust",
             "description": "QoS trust DSCP/CoS set", "severity": "info"},
        ],
    }),
    ("Switch Uplink Port Config", {
        "description": ("Ports connected to other switches should be trunk ports "
                        "with spanning-tree configured and no portfast."),
        # Real switches advertise BOTH bridge AND router; APs advertise bridge (+
        # wlan-ap). Requiring "router" matches switch-to-switch uplinks only and
        # excludes AP ports that also advertise bridge.
        "trigger": "lldp_capability", "trigger_value": "bridge", "platform": "",
        "trigger_require_capabilities": ["router"],
        "checks": [
            {"type": "config_contains", "value": "trunk",
             "description": "Port in trunk mode", "severity": "error"},
            {"type": "config_contains", "value": "spanning-tree",
             "description": "STP configured on uplink", "severity": "warning"},
            {"type": "config_not_contains", "value": "portfast",
             "description": "No portfast on uplink (security risk)", "severity": "warning"},
        ],
    }),
    ("Server / Workstation Port Config", {
        "description": ("Ports connected to servers/workstations (LLDP station "
                        "capability) should be access ports and should NOT have "
                        "portfast that bypasses STP."),
        "trigger": "lldp_capability", "trigger_value": "station", "platform": "",
        "checks": [
            {"type": "config_contains", "value": "access",
             "description": "Port in access mode", "severity": "warning"},
        ],
    }),
    ("Security Camera Port Config", {
        "description": ("Switch ports connected to security cameras, detected via "
                        "an interface-description naming convention."),
        "trigger": "interface_description",
        "trigger_value": "(?i)(cam|camera|nvr|dvr|cctv)", "platform": "",
        "checks": [
            {"type": "config_contains", "value": "access",
             "description": "Port in access mode", "severity": "error"},
            {"type": "config_not_contains", "value": "trunk",
             "description": "Not in trunk mode", "severity": "error"},
            {"type": "config_contains", "value": "surveillance",
             "description": "In surveillance/camera VLAN", "severity": "warning"},
        ],
    }),
    ("Printer / IoT Port Config", {
        "description": ("Switch ports connected to printers and IoT devices should "
                        "be access ports isolated on a dedicated VLAN."),
        "trigger": "interface_description",
        "trigger_value": "(?i)(print|printer|iot|scanner)", "platform": "",
        "checks": [
            {"type": "config_contains", "value": "access",
             "description": "Port in access mode", "severity": "error"},
            {"type": "config_not_contains", "value": "trunk",
             "description": "Not in trunk mode", "severity": "error"},
        ],
    }),
    ("VLAN SVI Config (AOS-CX)", {
        "description": "Check VLAN interfaces (SVIs) have a description configured.",
        "trigger": "interface_name", "trigger_value": r"^vlan\d+$", "platform": "aos_cx",
        "checks": [
            {"type": "config_contains", "value": "description",
             "description": "SVI has a description", "severity": "info"},
        ],
    }),
    ("LAG Interface Config (AOS-CX)", {
        "description": "Check LAG interfaces have LACP configured.",
        "trigger": "interface_name", "trigger_value": r"^lag\d+$", "platform": "aos_cx",
        "checks": [
            {"type": "config_contains", "value": "lacp",
             "description": "LACP configured on LAG", "severity": "warning"},
        ],
    }),
]

# (name, defaults-builder) — role consistency rules, all disabled. role looked up
# at seed time so the seeder works before/after roles exist.
ROLE_RULES = [
    ("Access Switch VLAN Consistency", "access-switch", {
        "description": ("All access switches should have the same VLANs configured. "
                        "Flags switches missing VLANs or with unexpected extras."),
        "check_type": "vlan_consistency", "platform": "aos_cx",
        "excluded_vlans": [1], "severity": "warning",
    }),
    ("Core Switch VLAN Consistency", "core-switch", {
        "description": "Core switches should have a consistent VLAN database.",
        "check_type": "vlan_consistency", "platform": "",
        "excluded_vlans": [1], "severity": "warning",
    }),
]


class Command(BaseCommand):
    help = "Seed example (disabled) interface-compliance and role-consistency rules."

    def handle(self, *args, **options):
        from apps.compliance.models import InterfaceComplianceRule, RoleConsistencyRule
        from apps.devices.models import DeviceRole

        created = 0
        for name, defaults in INTERFACE_RULES:
            _, was_created = InterfaceComplianceRule.objects.get_or_create(
                name=name, defaults={**defaults, "enabled": False})
            created += was_created
        for name, role_slug, defaults in ROLE_RULES:
            role = DeviceRole.objects.filter(slug=role_slug).first()
            _, was_created = RoleConsistencyRule.objects.get_or_create(
                name=name, defaults={**defaults, "role": role, "enabled": False})
            created += was_created

        self.stdout.write(self.style.SUCCESS(
            f"Compliance example rules seeded ({created} new, "
            f"{len(INTERFACE_RULES) + len(ROLE_RULES) - created} already present)."))
