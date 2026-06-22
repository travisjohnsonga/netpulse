# Configuration Push Templates

spane can push standardized configuration to network devices using Jinja2
templates. This keeps settings consistent across the fleet for SNMP, syslog,
NTP, banners, AAA, and more — change a template (or a global setting) once and
re-push it everywhere.

!!! warning "Config push is gated"
    Pushing config is disabled by default. Set `ALLOW_CONFIG_PUSH=true` in `.env`
    to enable it. While disabled, the push button still **records an audited
    attempt** but returns `403` and changes nothing. This is intentional —
    read-only monitoring is the safe default.

## Accessing templates

**Settings → Network Devices → Config Templates**

Templates are admin-only (rendering and pushing config to devices is a
privileged action).

## Built-in templates

spane ships editable, non-deletable built-in templates for common cases:

| Template          | Platform | Category |
|-------------------|----------|----------|
| AOS-CX SNMP v3    | aos_cx   | SNMP     |
| AOS-CX Syslog     | aos_cx   | Syslog   |
| AOS-CX NTP        | aos_cx   | NTP      |
| AOS-CX Banner     | aos_cx   | Banner   |
| Cisco IOS SNMP v3 | ios      | SNMP     |
| Cisco IOS Syslog  | ios      | Syslog   |

Built-in templates can be edited (content, variables, enabled state) but not
deleted — disable one instead if you don't want it to appear in the push picker.

## Creating a template

1. **Settings → Network Devices → Config Templates → + Add Template**
2. Choose a **category** and **platform** (leave platform blank to allow any
   platform).
3. Write the **Jinja2 template content**.
4. Fill in **default variables** (sensitive ones are masked — see below).
5. **Save**, then **Preview** the rendered output against a real device.

A template scoped to a platform will refuse to push to a device of a different
platform (the push result reports a "platform mismatch" for that device).

## Jinja2 template syntax

Templates use [Jinja2](https://jinja.palletsprojects.com/). spane renders them in
a **sandboxed** environment (no filesystem/builtin access).

```jinja2
{# Comment — stripped before pushing #}

{# Simple substitution #}
logging {{ syslog_server }}

{# Default value when a variable isn't provided #}
logging trap {{ syslog_severity | default('informational') }}

{# Conditional block #}
{% if syslog_port is defined %}
logging port {{ syslog_port }}
{% endif %}

{# Loop over a list #}
{% for server in ntp_servers %}
ntp server {{ server }} iburst
{% endfor %}
```

Comment lines (`#`, `!`) and blank lines are stripped from the rendered output
before it is sent to the device, and the result is reduced to ASCII so a stray
non-ASCII character can't corrupt a device config.

## Automatic variables

These are always available without defining them. `device` and `site` come from
the target device; `settings` comes from the platform's system-settings store
(and is blank for any key you haven't configured).

| Variable                   | Value                       | Example            |
|----------------------------|-----------------------------|--------------------|
| `device.hostname`          | Device hostname             | wco2-idf4-asw-01   |
| `device.management_ip`     | Management IP (or primary)  | 10.150.0.20        |
| `device.ip_address`        | Primary/identity IP         | 10.150.0.20        |
| `device.platform`          | Platform slug               | aos_cx             |
| `device.vendor`            | Vendor                      | HPE                |
| `device.site.name`         | Site name                   | Waco 2/3           |
| `device.role.name`         | Device role name            | Access Switch      |
| `settings.syslog_server`   | Configured syslog collector | 10.16.132.250      |
| `settings.ntp_primary`     | Primary NTP server          | 10.16.0.1          |
| `settings.ntp_secondary`   | Secondary NTP server        |                    |
| `settings.snmp_community`  | SNMP community              | public             |
| `settings.dns_primary`     | Primary DNS server          | 10.16.0.10         |
| `settings.domain_suffix`   | Domain suffix               | example.com        |

Any other `{{ name }}` you reference becomes an editable variable field in the
UI. spane auto-detects referenced variables from the template content.

### Example: AOS-CX syslog (driven by a global setting)

```jinja2
logging {{ settings.syslog_server }} severity informational vrf default
```

Because this reads the globally configured syslog server, you update one setting
to change every device's syslog target on the next push.

### Example: AOS-CX SNMPv3

```jinja2
snmpv3 user {{ snmp_user }} auth sha auth-pass {{ snmp_auth_pass }} priv aes priv-pass {{ snmp_priv_pass }}
```

## Pushing templates to devices

1. Click **▶ Push** on a template card.
2. Review/override the **variable values** (sensitive fields must be supplied
   here if they weren't stored).
3. Choose the **target devices**:
   - **All matching-platform devices**
   - **Devices in a specific site**
   - **Specific devices** (a checkbox list; mismatched platforms are disabled)
4. **Preview** the rendered config for the first target.
5. **Push** and confirm. The result lists each device with success/failure.

Each device is pushed over SSH (Netmiko), reusing the device's existing
credential profile and the SSH password from OpenBao — exactly the same
connection path as telemetry config push.

## Sensitive variables

Variables whose name contains `pass`, `key`, `secret`, `token`, or `cred` are
treated as sensitive:

- **Displayed as password fields** in the UI (masked).
- **Never logged** and **never returned** in API responses.
- **Never stored in the database.** When OpenBao is configured, a sensitive
  default value is stored there (at `netpulse/config_templates/{id}`); otherwise
  it is not persisted and must be supplied at push time.
- **Masked in previews** — the rendered preview replaces sensitive values with
  `●●●●●●` so secrets never reach the browser.

## Safety notes

- **Always preview before pushing**, and push to **one device first** to test.
- spane **audit-logs every push attempt** (success or failure, including pushes
  blocked by `ALLOW_CONFIG_PUSH=false`) under the `config_pushed` event type —
  see Settings → System → Audit Log.
- After a push, re-run a **config collection / compliance check** for the
  affected devices to capture the new running config and refresh their
  compliance score.
- A platform-scoped template will not push to a device of another platform.
