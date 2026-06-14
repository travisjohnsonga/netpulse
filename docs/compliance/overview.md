# Compliance Engine

spane checks network configuration at several layers and rolls them into a
single per-device score, plus a fleet-wide regulatory view.

## Device compliance score

Each device gets a weighted score over the components that **apply** to it; the
weights are renormalised by the sum of present components, so a switch with only
interface rules is still scored fairly.

| Component | Weight | What it measures |
|-----------|--------|------------------|
| Template Compliance | 50% | Config matches its Jinja2 role/platform/site template |
| Interface Rules | 30% | LLDP-aware per-port checks pass (see [interface-rules](interface-rules.md)) |
| Role Consistency | 20% | Device has the same VLANs/settings as its role peers |
| Running/Startup Match | 20% | Running config is saved to startup (no unsaved changes) |

Grades: **A** ≥90, **B** ≥80, **C** ≥70, **D** ≥60, **F** <60.

`GET /api/devices/{id}/compliance/` returns `score`, `grade`, `breakdown`, and
the detailed `template_findings` / `interface_rule_findings` /
`role_consistency_findings` / `startup_status` that the device **Compliance**
tab renders. (`overall_score` and `results` remain for backward compatibility.)

Interface-rule findings include the failing interface's config block and a
platform-specific **suggested fix** (copyable in the UI); role findings include
missing/extra/expected VLAN sets.

## Template compliance

Jinja2 templates define the expected config per device role/platform/site.
Findings are classified **MISSING**, **DRIFT**, or **EXTRA**.

## Interface & role rules

See **[interface-rules.md](interface-rules.md)** for LLDP-capability triggers,
description-pattern triggers, check types, and role-consistency (majority-vote)
details.

## Running vs startup config check

Detects **unsaved** configuration — when the running config differs from the
saved startup config, a reboot loads the stale startup config and the device can
come up misconfigured (a common post-outage incident).

- **AOS-CX** — compares `fullconfigs/running-config` vs `fullconfigs/startup-config` over REST.
- **Cisco IOS/IOS-XE** — `show archive config differences nvram:startup-config system:running-config`.

On a mismatch the latest `DeviceConfig` is stamped (`startup_match=false` +
`startup_diff`), the compliance score's Running/Startup component drops to 0, a
**"Startup config not saved"** alert fires (auto-resolves when saved), and the
device tab shows the diff with a copyable `write memory`. The dashboard shows an
**Unsaved Configs** banner.

## Config-collection health

Every collection **attempt** is recorded in `ConfigCollectionLog` (success /
unchanged / failed / timeout / auth_failed / empty) with duration and transport
method — so an unchanged-but-successful run still proves the device was reached.

- Per device: **Device → Configuration** tab (collection history table).
- Fleet: **Settings → Compliance → Config Health**, and a dashboard widget.
- APIs: `/api/configbackup/collection-log/`, `/api/configbackup/collection-stats/`,
  `/api/devices/{id}/collection-log/`.

## Regulatory frameworks

The **Compliance** page (`/compliance`) maps the operational evidence spane
already collects onto common framework controls and generates a **PDF evidence
package** for auditors.

Frameworks: **SOX (ITGC)**, **ISO/IEC 27001**, **NIST CSF 2.0**, **PCI-DSS 4.0**,
**HIPAA Security Rule**, **CIS Controls v8**.

Each control is mapped to an evidence collector that inspects live data (asset
inventory, config compliance, backups, change audit, running/startup, CVEs, OS
lifecycle, OpenBao secrets posture, RBAC, audit logging, TLS, segmentation) and
resolves to **satisfied / partial / gap / n/a**. Coverage is the weighted mean of
control scores. APIs: `GET /api/frameworks/`, `/api/frameworks/{key}/`,
`/api/frameworks/{key}/report/` (PDF).

> Control catalogs are **representative subsets** mapped to the signals spane can
> evidence — not verbatim reproductions of the full standards. Controls marked
> **PARTIAL** typically require manual attestation to fully satisfy. spane
> complements, but does not replace, a GRC platform.
