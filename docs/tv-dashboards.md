# TV Dashboards

Fullscreen, chrome-free dashboards for a NOC / network-operations monitor. They
require authentication but render outside the app shell (no sidebar or top nav),
so a kiosk browser can be pointed at a bookmarked URL and left running.

Access: `https://<your-spane>/tv`

## Available dashboards

| URL | Dashboard | Shows |
|---|---|---|
| `/tv/network` | Network Overview | device counts (online/down), per-site status, active alerts by severity |
| `/tv/wireless` | Wireless Overview | APs up/total, clients, satisfaction, per-AP grid |
| `/tv/security` | Security Events | recent audit events, failure / source-IP / user counts |
| `/tv/ops` | Operations Status | config-collection success rate, agent heartbeats, service checks, alerts |
| `/tv/sites` | Site Status | per-site online/total with the down devices listed |
| `/tv/servers` | Server Health | CPU / memory / disk across agent-monitored servers |
| `/tv/compliance` | Compliance Status | regulatory-framework coverage + unsaved/never-collected counts |

Each dashboard auto-refreshes on its own cadence (30–300 s) and shows a refresh
countdown in the header.

## Auto-rotation

From the `/tv` launcher, tick the dashboards to cycle through, choose an
interval, and **Start Rotation**. This opens:

```
/tv/rotate?screens=network,wireless,security&interval=30
```

Each screen then shows the next-screen name + countdown in the header and a
progress bar along the bottom. The URL is shareable/bookmarkable for a kiosk.

## Theme

Dark, high-contrast (`#0a0a0f` background, large stat numbers), designed for
readability across a room on a wall-mounted display.
