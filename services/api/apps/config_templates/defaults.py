"""Built-in config-push templates, seeded on migrate (idempotent).

These are marked ``builtin=True`` — editable in the UI but not deletable. The
seeder keys on ``name`` and only creates missing templates; it never overwrites
admin edits to an existing built-in.
"""

from __future__ import annotations

# Each entry: name, description, category, platform, template_content, variables.
# Sensitive variables (snmp_auth_pass, …) are intentionally NOT given stored
# defaults — they are supplied at push time and never persisted in the DB.
BUILTIN_TEMPLATES = [
    {
        "name": "AOS-CX SNMP v3",
        "description": "Configure an SNMPv3 user (SHA auth / AES priv) on AOS-CX switches.",
        "category": "snmp",
        "platform": "aos_cx",
        "template_content": (
            "snmpv3 user {{ snmp_user }} auth sha auth-pass {{ snmp_auth_pass }} "
            "priv aes priv-pass {{ snmp_priv_pass }}"
        ),
        "variables": {"snmp_user": ""},
    },
    {
        "name": "AOS-CX Syslog",
        "description": "Forward syslog to a collector over the default VRF on AOS-CX.",
        "category": "syslog",
        "platform": "aos_cx",
        "template_content": (
            "logging {{ syslog_server }} severity {{ syslog_severity | default('informational') }} vrf default\n"
            "{% if syslog_port is defined %}logging port {{ syslog_port }}{% endif %}"
        ),
        "variables": {"syslog_severity": "informational"},
    },
    {
        "name": "AOS-CX NTP",
        "description": "Configure NTP servers and enable NTP authentication on AOS-CX.",
        "category": "ntp",
        "platform": "aos_cx",
        "template_content": (
            "ntp server {{ ntp_primary }} iburst\n"
            "{% if ntp_secondary is defined %}ntp server {{ ntp_secondary }} iburst{% endif %}\n"
            "ntp enable\n"
            "ntp authentication enable"
        ),
        "variables": {},
    },
    {
        "name": "AOS-CX Banner",
        "description": "Set the login MOTD banner on AOS-CX switches.",
        "category": "banner",
        "platform": "aos_cx",
        "template_content": "banner motd !{{ banner_text }}!",
        "variables": {"banner_text": "Authorized access only."},
    },
    {
        "name": "Cisco IOS SNMP v3",
        "description": "Configure an SNMPv3 group + user (SHA auth / AES-128 priv) on IOS.",
        "category": "snmp",
        "platform": "ios",
        "template_content": (
            "snmp-server group {{ snmp_group }} v3 priv\n"
            "snmp-server user {{ snmp_user }} {{ snmp_group }} v3 auth sha {{ snmp_auth_pass }} "
            "priv aes 128 {{ snmp_priv_pass }}"
        ),
        "variables": {"snmp_group": "spane-rw", "snmp_user": ""},
    },
    {
        "name": "Cisco IOS Syslog",
        "description": "Forward syslog to a collector on Cisco IOS devices.",
        "category": "syslog",
        "platform": "ios",
        "template_content": (
            "logging host {{ syslog_server }}\n"
            "logging trap {{ syslog_severity | default('informational') }}\n"
            "logging on"
        ),
        "variables": {"syslog_severity": "informational"},
    },
    {
        # Keywords verified live on vSRX 23.2R2.21 (CLI completion): SHA-1 is
        # "authentication-sha" — "authentication-sha1" does NOT exist and Junos
        # rejects the line at that token (the config_gen _AUTH_CLI bug fixed in
        # the same change that seeded these). Pushes to Junos go through
        # push_junos_private (configure private + explicit commit).
        "name": "Junos SNMP v3",
        "description": "Configure an SNMPv3 user (SHA auth / AES-128 priv) with read-only VACM access on Junos.",
        "category": "snmp",
        "platform": "junos",
        "template_content": (
            "set snmp v3 usm local-engine user {{ snmp_user }} authentication-sha authentication-password {{ snmp_auth_pass }}\n"
            "set snmp v3 usm local-engine user {{ snmp_user }} privacy-aes128 privacy-password {{ snmp_priv_pass }}\n"
            "set snmp v3 vacm security-to-group security-model usm security-name {{ snmp_user }} group {{ snmp_group | default('V3GROUP') }}\n"
            "set snmp v3 vacm access group {{ snmp_group | default('V3GROUP') }} default-context-prefix security-model usm security-level privacy read-view all\n"
            "set snmp view all oid .1 include"
        ),
        "variables": {"snmp_user": "", "snmp_group": "V3GROUP"},
    },
    {
        "name": "Junos Syslog",
        "description": "Forward syslog to a collector on Junos devices.",
        "category": "syslog",
        "platform": "junos",
        "template_content": (
            "set system syslog host {{ syslog_server }} any {{ syslog_severity | default('info') }}\n"
            "set system syslog host {{ syslog_server }} port {{ syslog_port | default('514') }}"
        ),
        "variables": {"syslog_severity": "info", "syslog_port": "514"},
    },
    {
        "name": "Junos NTP",
        "description": "Configure NTP servers on Junos devices.",
        "category": "ntp",
        "platform": "junos",
        "template_content": (
            "set system ntp server {{ ntp_primary }}\n"
            "{% if ntp_secondary is defined %}set system ntp server {{ ntp_secondary }}{% endif %}"
        ),
        "variables": {},
    },
    {
        "name": "Junos Banner",
        "description": "Set the pre-login system message on Junos devices.",
        "category": "banner",
        "platform": "junos",
        "template_content": 'set system login message "{{ banner_text }}"',
        "variables": {"banner_text": "Authorized access only."},
    },
    {
        "name": "FortiOS SNMP v3",
        "description": "Configure an SNMPv3 user (SHA-1 auth / AES priv) on FortiGate firewalls.",
        "category": "snmp",
        "platform": "fortios",
        "template_content": (
            "config system snmp sysinfo\n"
            "    set status enable\n"
            "end\n"
            "config system snmp user\n"
            '    edit "{{ snmp_user }}"\n'
            "        set security-level auth-priv\n"
            "        set auth-proto sha1\n"
            "        set auth-pwd {{ snmp_auth_pass }}\n"
            "        set priv-proto aes\n"
            "        set priv-pwd {{ snmp_priv_pass }}\n"
            "    next\n"
            "end"
        ),
        "variables": {"snmp_user": ""},
    },
    {
        "name": "FortiOS Syslog",
        "description": "Forward syslog to a collector on FortiGate firewalls.",
        "category": "syslog",
        "platform": "fortios",
        "template_content": (
            "config log syslogd setting\n"
            "    set status enable\n"
            '    set server "{{ syslog_server }}"\n'
            "    set port {{ syslog_port | default('514') }}\n"
            "    set facility local7\n"
            "end"
        ),
        "variables": {"syslog_port": "514"},
    },
]


def seed_builtin_templates(model) -> int:
    """Create any missing built-in templates. Returns the number created.

    ``model`` is passed in so this works from both a data migration (historical
    model) and the management command (live model).
    """
    created = 0
    for spec in BUILTIN_TEMPLATES:
        _obj, was_created = model.objects.get_or_create(
            name=spec["name"],
            defaults={
                "description": spec["description"],
                "category": spec["category"],
                "platform": spec["platform"],
                "template_content": spec["template_content"],
                "variables": spec["variables"],
                "builtin": True,
                "enabled": True,
            },
        )
        if was_created:
            created += 1
    return created
