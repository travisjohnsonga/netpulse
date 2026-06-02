# Contributing MIB Files

MIB files enable NetPulse to resolve SNMP OIDs to human-readable names and
discover device capabilities.

## Directory Structure

- `standard/`  — RFC standard MIBs (SNMPv2-SMI, IF-MIB, IP-MIB, …)
- `vendor/`    — vendor-provided MIBs, organised by vendor (`vendor/cisco/`, …)
- `vendor/community/` — community-contributed MIBs
- `custom/`    — site-specific MIBs (git-ignored, never committed)

All directories are mounted read-only into the `ingest-snmp` service and
read-write into `api` (so uploads land in `custom/`).

## Contributing a MIB

1. Obtain the MIB file from your vendor.
2. Place it in the appropriate directory, e.g.
   `vendor/<vendor_name>/VENDOR-MIB-NAME.my`.
3. Validate it: `docker compose exec api python manage.py validate_mib <file>`.
4. Submit a pull request.

## Supported Formats

- `.my`  — ASN.1 MIB format (most common)
- `.mib` — alternative extension
- `.txt` — some vendors ship MIBs as `.txt`

## Important Notes

- Only contribute MIBs you have the rights to distribute.
- Vendor MIBs are usually freely downloadable from vendor support portals —
  see each `vendor/<name>/README.md` for download links.
- Do **not** commit MIBs containing proprietary configuration data.
- Custom/site-specific MIBs belong in `custom/` (git-ignored).

## Standard MIBs to include

Place these in `standard/` (freely available from the IANA / IETF):

- `SNMPv2-SMI`, `SNMPv2-TC`, `SNMPv2-MIB`
- `RFC1213-MIB` (MIB-II)
- `IF-MIB` (interfaces)
- `IP-MIB`, `TCP-MIB`, `UDP-MIB`
- `HOST-RESOURCES-MIB` (`hrProcessorLoad`, …)
- `ENTITY-MIB` (hardware inventory)

Many vendor MIBs cannot be redistributed here due to licensing — download them
from the vendor and drop them in the matching `vendor/<name>/` directory.
