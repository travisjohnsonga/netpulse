# UniFi Integration

Import Ubiquiti UniFi-managed devices (APs, switches, gateways) into NetPulse.
Each UniFi controller (often one per site) is registered once; NetPulse polls
each enabled controller and imports its devices.

Configure under **Settings → Integrations → UniFi**.

## Cloud auto-discovery (recommended)

Instead of adding every controller by hand, provide a single **UniFi Site
Manager** API key and let NetPulse discover all your controllers.

1. Generate an API key at **unifi.ui.com → Account → API Keys**.
2. In NetPulse → Settings → Integrations → UniFi → **UniFi Cloud Account**, paste
   the key and click **Test Connection** (shows the number of hosts found).
3. Click **Discover Controllers**. NetPulse calls the Site Manager API
   (`https://api.ui.com/v1/hosts`, paginated) and creates/updates a controller
   record per host — host IP from `reportedState.ipAddresses`, port `443` for
   consoles (UDM/UDR) or `8443` for CloudKeys.

The API key is stored in OpenBao (`netpulse/integrations/unifi/cloud`), never in
the database.

!!! note
    The cloud API lists controllers but **not** their managed devices. After
    discovery you must add **local credentials** to each controller (below)
    before device sync works.

## Local controller credentials

For each controller, set:

- **Host / Port** — auto-filled by cloud discovery; editable.
- **Username / Password** — a local UniFi controller account (stored in OpenBao
  at `netpulse/integrations/unifi/{id}`).
- **UniFi Site ID** — `default` unless you renamed it (UniFi → Settings → System
  → Advanced → Site ID).
- **Assign to Site** — the NetPulse site imported devices are placed in.
- **Verify SSL** — off by default (controllers use self-signed certs).

Use **Test Connection** to verify and see the device count.

## Device sync

- **Sync** (per controller) or **Sync All** imports devices immediately.
- The scheduler also syncs every enabled controller every 6h
  (`UNIFI_SYNC_INTERVAL_S`).
- Device types map as: `uap → unifi_ap` / Wireless AP, `usw → unifi_sw` / Access
  Switch, `ugw → unifi_gw` / Router, `udm → unifi_udm` / Router. Devices are
  keyed by IP, so re-syncs update in place.
