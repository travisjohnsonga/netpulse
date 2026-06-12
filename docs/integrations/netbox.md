# NetBox Integration

Import sites and devices from [NetBox](https://netbox.dev). Configure under
**Settings → Integrations → NetBox**.

## Requirements

- **NetBox 4.5 or later**
- A **v2 API token** (Key ID in the `nbt_` format)

Legacy v1 API tokens are no longer supported — NetPulse authenticates with the
NetBox 4.5+ v2 `Bearer` scheme. If you only have a legacy token, generate a new
v2 token (below).

## Generating a v2 API Token

1. Log into NetBox.
2. Click your username → **Profile**.
3. Go to **API Tokens → Add Token**.
4. Copy **both** values:
   - **Key ID** — shown in the *Key* column (starts with `nbt_`).
   - **Token** — the secret, shown **once** immediately after creation.
5. Enter both in NetPulse under **Settings → Integrations → NetBox**:
   - **API Key ID** = the `nbt_…` key.
   - **API Token** = the secret value.

   Then click **Test** to verify the connection and detected NetBox version.

> ⚠️ The token secret is shown **only once** at creation time. Store it securely
> — if you lose it, generate a new token.

NetPulse combines the two values as `{key}.{secret}` and writes the result to
OpenBao (`netpulse/integrations/netbox/{id}`); only the path is stored in the
database, never the credential itself.

## SSL Configuration

If NetBox uses a self-signed certificate, **uncheck "Verify SSL Certificate"** in
the NetBox integration settings. Leave it checked for any NetBox reachable over a
publicly trusted (or internally trusted) certificate.

## DNS Resolution

If NetBox is reached by an internal hostname (e.g. `netbox.company.local`), the
NetPulse containers must be able to resolve it. Set `INTERNAL_DNS` (and the
search domain) in `.env`:

```bash
INTERNAL_DNS=10.x.x.x        # your internal DNS server
INTERNAL_DOMAIN=company.local
# INTERNAL_DOMAIN2=          # optional second search domain
```

`setup.sh` auto-detects these from the host (`resolvectl status`); override them
if auto-detection is wrong. Containers then use this DNS server to resolve
internal hostnames. See `docs/setup/deployment.md` for the full DNS notes.

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
