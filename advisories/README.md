# Community advisory feeds

Vendors without a machine-readable advisory API (Juniper JSAs, Arista
advisories, and others) are tracked here as community-maintained YAML. NetPulse
loads these with `python manage.py load_community_advisories` (run by the
cve-engine, or manually), upserting `CVE` records and correlating them to
matching devices.

## Layout

```
advisories/
  juniper/   *.yaml
  arista/    *.yaml
```

One file may contain one or many advisories under an `advisories:` list.

## Schema

```yaml
advisories:
  - id: JSA79134                       # vendor advisory id (used as the CVE id if no cve_ids)
    cve_ids: [CVE-2024-21619]          # optional; the first is used as the primary id
    title: Short title
    description: Longer description.
    severity: critical|high|medium|low
    cvss_score: 5.9                    # optional
    cvss_vector: "CVSS:3.1/AV:N/..."   # optional
    published: 2024-01-10              # YYYY-MM-DD
    url: https://...                   # advisory link
    affected:
      vendor: juniper                  # informational
      platforms: [junos]               # NetPulse Device.platform values (ios_xe, junos, eos, …)
      versions: ["<21.4R3-S5", "22.1"] # informational hints (not yet used for range matching)
```

## Correlation

Active devices whose `platform` is in `affected.platforms` are linked to the
advisory's CVE (a `DeviceCVE` row) so they surface on the device/CVE pages.
Version-range matching is a planned refinement — for now a platform match flags
a device as potentially affected for review.

## Contributing

Add a YAML file under the vendor directory and open a PR. Keep `cve_ids`
accurate where a CVE exists; otherwise the vendor advisory id is used.
