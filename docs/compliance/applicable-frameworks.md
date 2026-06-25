# Applicable Frameworks (compliance scope)

By default spane shows **every** regulatory framework it ships. In most
environments only a subset actually applies — a hospital network is subject to
HIPAA, a cardholder-data environment to PCI-DSS, a public company's financial
systems to SOX, and so on. `APPLICABLE_COMPLIANCE_FRAMEWORKS` lets an operator
declare which frameworks this environment is subject to.

## Why scope frameworks

Frameworks you are **not** subject to should never read as a problem to the
people who look at compliance: auditors, compliance officers, and management. An
out-of-scope framework left visible shows up with unmet controls — red **Gap** /
amber **Partial** badges and a low coverage percentage — which looks like
"we're failing PCI-DSS" when in fact PCI-DSS simply doesn't apply.

Scoping removes out-of-scope frameworks from **every** compliance surface, so the
only frameworks anyone sees are ones where a non-green status is a **real,
actionable finding**:

- the **Compliance** page (`/compliance`) cards and control drill-in,
- the **TV/NOC** compliance screen (`/tv/compliance`) — including its
  *Fleet Coverage* average and *Frameworks* count,
- the fleet-coverage **aggregate** and any **"N frameworks"** count (the
  denominator is the count of *applicable* frameworks),
- the per-framework **PDF evidence package** (an out-of-scope framework can't be
  assessed or exported — a report handed to an auditor contains only applicable
  frameworks),
- and the `GET /api/frameworks/...` API the above are built on (out-of-scope
  frameworks are absent from the list and return **404** on direct access).

A small number of evidence checks are **unique** to a single framework (for
example, PCI-DSS's network-segmentation control). When that framework is out of
scope its unique checks are never evaluated, so they never drag down the fleet
picture — only in-scope frameworks contribute to the headline numbers.

> The device **compliance score** (config/template/interface/role/startup) is a
> separate signal and is **not** affected by framework scope — it never counted
> regulatory-framework controls in the first place. Scoping only governs the
> regulatory-framework surfaces described above.

## Supported frameworks

These are the framework **keys** spane ships (the values you put in the list),
with their display name and version. *(Verified against
`apps/frameworks/models.py` `RegulatoryFramework.Key` and
`apps/frameworks/management/commands/seed_frameworks.py`.)*

| Key | Framework | Version |
|-----|-----------|---------|
| `sox` | SOX (ITGC) | 2024 |
| `iso27001` | ISO/IEC 27001 | 2022 |
| `nist_csf` | NIST CSF | 2.0 |
| `pci_dss` | PCI-DSS | 4.0 |
| `hipaa` | HIPAA Security Rule | 2013 |
| `cis` | CIS Controls v8 | 8.0 |

## How to enable

Set the comma-separated list of applicable framework keys in your `.env`:

```bash
# Only SOX and ISO 27001 apply to this environment:
APPLICABLE_COMPLIANCE_FRAMEWORKS=sox,iso27001
```

Behaviour:

- **Set** → only the listed frameworks apply; all others are removed from every
  surface above.
- **Unset or empty** → **all** frameworks apply (the back-compat default). You
  opt **into** scoping; leaving it blank keeps every framework.
- Keys are matched case-insensitively; unknown keys are ignored (a typo can't
  silently widen scope to a non-existent framework — if *all* listed keys are
  invalid, nothing is shown rather than everything).
- It is **env-loaded**, so it requires an application **restart** to take effect
  (`./netpulse.sh restart`).

Scoping is deliberately **operator-controlled** (`.env`, not a web toggle) so
compliance scope can't be changed casually from the UI.

## Scope as a Statement of Applicability

Declaring your applicable frameworks in configuration is, in effect, documenting
your compliance **scope** — the same idea as an ISO 27001 *Statement of
Applicability*. Keeping it in version-controlled config (rather than an ad-hoc UI
setting) gives you an auditable record of which frameworks the environment is
held to, and why a given framework does or doesn't appear in a report.

## See also

- [Compliance Engine](overview.md) — device score + the regulatory framework view.
- [Compliance Overview](../security/compliance-overview.md) and
  [Compliance Control Mapping](../security/compliance-mapping.md) — the ISO/SOX
  control mapping; the frameworks named there are the ones you scope here.
