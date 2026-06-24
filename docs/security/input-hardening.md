# Input Hardening

This page covers the controls that defend the places where spane takes a URL, an
XML document, a template, or a subnet from a user or an external system.

## SSRF guard on outbound URLs

`validate_outbound_url` (`apps/core/net_safety.py`) is the choke point for
server-side requests:

```python
def validate_outbound_url(url: str, *, block_metadata: bool = True) -> str:
```

It enforces two things:

1. **Scheme allowlist** — only `http` and `https`; everything else
   (`file://`, `ftp://`, `gopher://`, `dict://`, …) raises `UnsafeURLError`.
2. **Cloud-metadata block** (when `block_metadata=True`) — it resolves the host
   via `socket.getaddrinfo` and rejects any address that is a known
   cloud-metadata endpoint: `169.254.169.254` (AWS/GCP/Azure/OpenStack IMDS),
   `169.254.170.2` (AWS ECS task metadata), `100.100.100.200` (Alibaba), and
   `fd00:ec2::254` (AWS IMDS over IPv6). This prevents an attacker-supplied URL
   from coaxing the server into reading instance credentials.

It intentionally does **not** block private/RFC-1918 or loopback ranges, because
legitimate targets (on-prem NetBox, a local Ollama NLP backend, internal health
probes) live there.

Call sites:

| Caller | File | `block_metadata` |
|--------|------|------------------|
| ChatOps NLP backends (local + API) | `apps/chatops/nlp.py` | `True` (default) |
| NetBox API client | `apps/integrations/netbox.py` | `False` (admin-set URL) |
| Health probes (OpenBao, generic HTTP) | `apps/core/views.py` | `False` (admin/internal) |

The metadata block is on for the user-influenced ChatOps path and off for
admin-configured internal endpoints, where the scheme check alone is wanted.

## XML parsing (XXE)

Discovery parses nmap's XML output with `defusedxml`, the XXE- and
entity-expansion-safe drop-in for the stdlib parser
(`apps/devices/management/commands/run_discovery.py`):

```python
import defusedxml.ElementTree as ET  # XXE/entity-expansion-safe drop-in
from defusedxml.common import DefusedXmlException
```

This is used for host parsing, service parsing, and OS detection. A malformed or
malicious document raises `DefusedXmlException` / `ParseError`, which is caught
and degraded to an empty result rather than propagated. External entities,
DOCTYPE-driven remote fetches, and billion-laughs expansion are all blocked.

## Template sandboxing (SSTI)

Both template-rendering paths use Jinja2's `SandboxedEnvironment`, which blocks
access to unsafe attributes and builtins — so a template author cannot escalate
to arbitrary code execution:

- **Compliance templates** (`apps/compliance/engine.py`) — these are editable by
  the engineer/api roles and rendered server-side, so an unsandboxed environment
  would be an SSTI→RCE vector.
- **Config-push templates** (`apps/config_templates/render.py`).

`autoescape` is off in both because the output is device-config text that is
diffed, not HTML served to a browser.

## Subnet / argument-injection validation

Discovery job subnet fields are validated as real CIDRs/IPs before they ever
reach the nmap command line (`apps/devices/serializers.py`):

```python
ipaddress.ip_network(s, strict=False)
```

This rejects values like `--script=...` or `-oN` that would otherwise be passed
to nmap as argv flags — i.e. authenticated nmap-option injection. It is applied
to `subnets`, `excluded_subnets`, and `allowed_subnets`.

Relatedly, discovery builds subprocess commands as **argument arrays** (no
`shell=True`) and uses `--` to terminate option parsing
(`run_discovery.py`), so an IP that begins with `-` can't be misread as a flag.

## CSV formula injection

User-influenced text exported to CSV (audit-log usernames, descriptions, device
hostnames, finding text) is neutralized by `csv_safe` (`apps/core/audit.py`),
which prefixes a cell that begins with `= + - @` (or a leading tab/CR) with a
single quote so a spreadsheet treats it as text rather than a formula:

```python
def csv_safe(value) -> str:
    s = "" if value is None else str(value)
    if s[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + s
    return s
```

The audit-log CSV export applies it per cell, and report CSVs route every cell
through the reusable `_SafeCsvWriter` (`apps/reports/render.py`).
