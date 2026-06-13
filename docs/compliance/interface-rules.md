# Interface & Role Compliance Rules

Two config-compliance features that go beyond whole-device template matching:

- **Interface Rules** — LLDP-aware per-interface checks ("is every AP port
  configured correctly?").
- **Role Consistency** — cross-device drift detection ("do all access switches
  have the same VLANs?").

Both live under **Settings → Compliance** and ship with disabled example rules
that an admin reviews and enables.

## Interface Compliance Rules

A rule selects switch interfaces with a **trigger**, then runs a list of config
**checks** against each matching interface's config block (pulled from the latest
config backup).

### LLDP Capability triggers (recommended)

The most powerful trigger type. It uses the LLDP capability advertised by the
*neighbour* device to identify the port type automatically — so it works for any
vendor without maintaining platform lists.

| Capability (`trigger_value`) | Device type | Use case |
|------------------------------|-------------|----------|
| `wlan-access-point`          | Wireless APs | AP port config (portfast, no trunk, PoE) |
| `telephone`                  | IP phones | Voice VLAN, QoS |
| `bridge`                     | Switches | Trunk/uplink config |
| `station`                    | PCs / servers | Access-port config |
| `router`                     | Routers | Routed-port config |

> `wlan-access-point` catches **all** APs (UniFi, Mist, Cisco, Aruba …) because
> every AP advertises that LLDP capability. The value is normalised internally to
> the canonical `wlan-ap` token, so spelling variants all match.

### Other triggers

- **LLDP Neighbor Platform** — `trigger_value` is a comma-separated platform list
  (`unifi_ap,mist_ap`); matches ports whose neighbour resolves to one of those
  device platforms.
- **LLDP Neighbor Role** — comma-separated NetPulse role slugs.
- **Interface Description Pattern** — a regex matched against the interface
  `description`. Use this for devices that don't advertise LLDP (cameras, IoT,
  printers): e.g. `(?i)(cam|camera|nvr|dvr|cctv)`.
- **Manual Tag** — explicit `hostname:interface` pairs.

A **switch platform filter** (e.g. `aos_cx`) optionally limits a rule to one
switch platform.

### Checks

Each check is `{type, value, severity, description}`:

| Type | Passes when |
|------|-------------|
| `config_contains` | the interface config contains `value` |
| `config_not_contains` | the interface config does **not** contain `value` |
| `vlan_check` (`vlan_type: access`/`trunk`) | the interface is in that mode |

### API

```
GET    /api/compliance/interface-rules/
POST   /api/compliance/interface-rules/
PUT    /api/compliance/interface-rules/{id}/
DELETE /api/compliance/interface-rules/{id}/
POST   /api/compliance/interface-rules/{id}/run/     → results for all matching interfaces
GET    /api/compliance/interface-results/?device_id=&rule_id=
```

## Role Consistency Rules

Compares one piece of config across every device sharing a **role / platform /
site** and flags drift. The "expected" value set is the **majority vote** across
the group, so a single misconfigured switch stands out.

Check types: `vlan_consistency` (fully parsed; supports `excluded_vlans` for
per-switch management VLANs), plus `ntp_consistency`, `dns_consistency`,
`snmp_consistency`, `aaa_consistency`, and `banner_consistency`.

For VLAN drift the result includes a remediation snippet, e.g. add `vlan 40` /
remove `no vlan 999`.

### API

```
GET    /api/compliance/role-rules/
POST   /api/compliance/role-rules/
PUT    /api/compliance/role-rules/{id}/
DELETE /api/compliance/role-rules/{id}/
POST   /api/compliance/role-rules/{id}/run/          → expected set + per-device drift
```

## Seeded examples

`manage.py seed_compliance_rules` (run from the api entrypoint) seeds these as
**disabled**: Wireless AP Port Config, AP PoE Priority, IP Phone Port Config,
Switch Uplink Port Config, Server/Workstation Port Config, Security Camera Port
Config, Printer/IoT Port Config (interface rules); Access/Core Switch VLAN
Consistency (role rules).
