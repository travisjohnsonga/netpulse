# NetBox Integration

Import sites and devices from [NetBox](https://netbox.dev) (v3.x and v4.x are
auto-detected). Configure under **Settings → Integrations → NetBox**.

## Connection setup

1. In NetBox, create an API token (Admin → API Tokens).
2. In NetPulse → Settings → Integrations → NetBox → **Import**, enter the NetBox
   URL and the API token, then **Test** to verify the connection/version.

The token is written to OpenBao (`netpulse/integrations/netbox/{id}`); only the
path is stored in the database.

## Import preview

Click **Preview** before importing. NetPulse does a dry-run (no writes) and shows,
per device:

- **Create / Update / Skip** with a reason for skips (e.g. *No IP address in
  NetBox*, or an IP already used by another device).
- For updates, the fields that would change (platform, site, role, status, model).
- The **credential profile** each device would inherit from its site (see below).
- A **credential assignments** summary, including how many devices have no match.

Filter the list by action and search. The **Import N devices** button imports the
create/update rows (skips are excluded).

## Site credential mapping

On import, each device's site is matched and — if the site has
[credential assignments](unifi.md) — a matching `CredentialProfile` is applied
automatically (role-specific rule first, then a site-wide rule). An explicit
credential on an existing device is never overridden. Configure these under a
**Site → Credential Profiles**.

NetBox roles are matched to existing NetPulse `DeviceRole`s by name; an unmatched
role is left unset (the site-wide credential still applies).
