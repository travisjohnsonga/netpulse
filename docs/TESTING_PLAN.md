# spane Testing Plan

> Systematic test procedure for validating spane after fresh install,
> upgrades, or factory reset. Follow in order — each section builds on the
> previous.

---

## Pre-Test Setup

### Environment Requirements
- spane server running (all services Up)
- At minimum 1 Cisco IOS-XE device
- Recommended: router1, router2, fortinet1
- Web browser (Chrome/Firefox recommended)
- SSH client
- SNMP walk tool (optional)

### Start Fresh
```bash
./scripts/factory-reset.sh      # type RESET to confirm
./scripts/setup.sh              # set admin password (12+ chars)
./netpulse.sh rebuild-api       # wait ~30s for services
```

### Verify Services
```bash
./netpulse.sh status | grep -E "Up|Restart|unhealthy"
# Expected: all services Up, 0 restarting
```

### Access UI
```
https://{SERVER_IP}
# Accept self-signed cert warning
# Login: admin / {password set in setup.sh}
```

### Development port testing (optional)
Set non-privileged ports before starting:
```bash
# in .env
FRONTEND_PORT=3000
FRONTEND_HTTPS_PORT=3443
docker compose down && docker compose up -d
```
Verify:
```bash
curl -s -o /dev/null -w "%{http_code}\n" http://{SERVER_IP}:3000/    # 301/302 → HTTPS
curl -sk -o /dev/null -w "%{http_code}\n" https://{SERVER_IP}:3443/  # 200
```

---

## 1. First Run Experience

### 1.1 Setup Page
- [ ] Before setup.sh: UI shows setup-required page
- [ ] setup.sh completes without errors
- [ ] After setup.sh: UI redirects to login
- [ ] Login with admin credentials works
- [ ] Dashboard loads without errors

### 1.2 Initial Dashboard
- [ ] Dashboard loads (no JS errors in browser console)
- [ ] Dark mode toggle works
- [ ] Sidebar navigation links all work
- [ ] No "undefined" or empty error states

---

## 2. Credential Profiles

### 2.1 Create Cisco Credential Profile
Navigate: Settings → Credentials → + New Profile
- [ ] Name: "Cisco Lab"
- [ ] SSH: username, password, port=22
- [ ] SNMPv3: username, SHA auth, AES priv, auth+priv keys
- [ ] Create Profile → success, appears in list
- [ ] Stored securely (OpenBao 200 in logs, no 403)

### 2.2 Create Fortinet Credential Profile
- [ ] Name: "Fortinet Lab", SSH + SNMP as applicable → success

### 2.3 Verify OpenBao Storage
```bash
docker compose logs api | grep -i "openbao\|vault" | grep -v DEBUG | tail -5
# 200 responses, no 403 errors
```

---

## 3. Device Discovery

### 3.1 Active Scan
Navigate: Settings → Discovery → + New Job
- [ ] Method: Active Scan, subnet 192.168.98.0/24, credentials Cisco Lab
- [ ] OT/ICS warning visible in excluded subnets
- [ ] Run → pending → running, progress bar + "Scanned X IPs" + ETA update
- [ ] Completes ✅; discovered devices populated
- [ ] router1/router2: platform=ios_xe, vendor=cisco
- [ ] fortinet1: platform=fortios, vendor=fortinet
- [ ] Windows/Linux endpoints filtered out
- [ ] Unknown devices shown with platform selector

### 3.2 Topology Walk
- [ ] Method: Topology Walk, seed router1, credentials Cisco Lab
- [ ] Finds router1 + router2 via LLDP; no non-network devices

### 3.3 Device Approval
- [ ] Select router1 + router2 → Approve Selected → approved
- [ ] fortinet1 → Approve → platform selector → fortios → success
- [ ] All 3 in Devices list, Active within 30s

### 3.4 Already In Inventory
- [ ] Re-run discovery → existing devices show "Already in inventory →"
- [ ] No duplicates; badge links to device page

---

## 4. Post-Approval Enrichment

### 4.1 Automatic Enrichment (within ~60s)
- [ ] router1: model + os_version populated; platform=ios_xe (corrected from ios)
- [ ] router2: same; fortinet1: vendor/platform populated

### 4.2 Interface Auto-Discovery
- [ ] router1 → Telemetry: interfaces listed
- [ ] LLDP-connected interfaces auto-enabled (poll_traffic=true)

### 4.3 LLDP Topology
- [ ] Topology: router1 ↔ router2 links visible (2 links, no duplicates)
- [ ] Hover shows interface names; click node → device page

### 4.4 Initial Config Collection
- [ ] router1 → Configuration: a baseline (collected_by=enrichment) appears

---

## 5. Telemetry Validation

### 5.1 gNMI Streaming (Cisco IOS-XE)
- [ ] 📡 gNMI badge in header; "SNMP polling suppressed"
- [ ] CPU / Memory / Uptime show real values (not "no data")
- [ ] Ping latency chart + Overview Ping tile populated

### 5.2 Interface Traffic
- [ ] In/Out bps, util %, errors/drops shown; sparkline; time selector works

### 5.3 SNMP Fallback (Fortinet)
- [ ] 📊 SNMP badge; CPU (fgSysCpuUsage), Memory (fgSysMemUsage), Uptime show

### 5.4 Collection Status API
```bash
curl -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/api/devices/{id}/collection-status/
```
- [ ] Cisco: gnmi.active=true, snmp.suppressed=true
- [ ] fortinet1: gnmi.active=false, snmp.active=true

### 5.5 Adaptive Polling
- [ ] Valkey heartbeat exists: `keys "gnmi:last_seen:*"`
- [ ] ingest-snmp logs: "gNMI active for device X - polling essential OIDs only"
- [ ] Uptime still updates (sysUpTime polled even when gNMI active)

---

## 6. Configuration Management

- [ ] Manual "Collect Config Now" → config appears < 30s, syntax highlighted
- [ ] Collect twice → "unchanged"; change on device → diff shown
- [ ] Version history shows multiple entries
- [ ] Scheduled collection configured at 07:00 / 19:00 UTC (config-manager)
- [ ] (If ALLOW_CONFIG_PUSH=true) generated config is ASCII (no em/en dashes)

---

## 7. Log Ingestion

- [ ] SSH to router1 → router1 → Logs: session log appears < 30s, severity correct
- [ ] Severity / time / text filters work, individually and combined
- [ ] FortiOS logs normalized (traffic/event), not raw key=value
- [ ] Fleet Logs (main menu): device/severity/time filters work

---

## 8. Alerts

- [ ] Alerts → Rules: system rules show 🔒 (enable/disable only, no delete)
- [ ] Device unreachable → HIGH alert fires; restore → auto-resolves
- [ ] Alert Routing: team + escalation policy + route; test route → email
- [ ] Active/Resolved/All toggle; sidebar badge counts active only
- [ ] Maintenance window suppresses alerts for its devices during the window

---

## 9. Service Checks

- [ ] Create http/tcp/icmp/dns/tls/ssh_banner checks; Run Now → Up + response time
- [ ] History panel: response-time chart, status timeline, uptime %, period selector
- [ ] Search / type / status filters; Edit check updates without reload

---

## 10. Topology

- [ ] Map: router1 + router2, 2 links (not 4 duplicates)
- [ ] Node/edge hover tooltips; click node → device
- [ ] Discover Links: no duplicate links created on re-discovery
- [ ] Completed job panel: duration, scanned, links; Run Again / Delete

---

## 11. Discovery Page

- [ ] Allowed/excluded subnet management; OT/ICS warning; "Copy from subnets"
- [ ] Edit / Cancel / Restart / Delete job all work

---

## 12. Security

### 12.1 Auth Rate Limiting
```bash
for i in {1..7}; do
  curl -s -o /dev/null -w "Attempt $i: %{http_code}\n" \
    -X POST http://localhost:8000/api/auth/token/ \
    -H 'Content-Type: application/json' \
    -d '{"username":"wrong","password":"wrong"}'
done
# Expected: first attempts 401, then 429 once the throttle trips
```

### 12.2 HTTPS Enforcement
```bash
curl -s -o /dev/null -w "%{http_code}\n" http://{SERVER_IP}/
# Expected: 301/302 redirect to HTTPS
```

### 12.3 Credential Security
- [ ] Credentials never in API responses or logs; secrets only in OpenBao

---

## 13. UI/UX Checks

- [ ] Dark mode: all pages, modals, the device Settings dropdown readable
- [ ] Device list sortable/filterable/searchable; pagination; toasts; empty states
- [ ] Settings → General → Hostname Display: strip toggle works; full name in tooltip; persists

---

## 14. Performance Checks

- [ ] `./netpulse.sh status | grep -E "Restart|unhealthy"` → none
- [ ] InfluxDB receiving telemetry (>1000 records / 5 min across devices)
- [ ] Device list < 1s, detail < 2s, topology < 3s, log search < 3s, config < 30s

---

## 15. Factory Reset Validation

- [ ] `./scripts/factory-reset.sh` (type RESET) → post-reset "run setup.sh" message
- [ ] After setup.sh: 0 devices/alerts/checks/configs; InfluxDB + OpenSearch empty
- [ ] Admin account recreated

---

## 15a. NAT / Networking

- [ ] SNMP works from container to device
- [ ] Host IP visible on device SNMP logs (not the Docker subnet IP)
- [ ] NAT rule persists after reboot (`sudo iptables -t nat -L POSTROUTING -n`)
- [ ] `sudo ./netpulse.sh fix-nat` reapplies the rule (idempotent — second run
      reports "already configured")
- [ ] Health check reports the NAT rule status
      (`./netpulse.sh health` → "Docker NAT"; warns from inside the container,
      fails on the host when the rule is missing)

---

## 16. AOS-CX (HPE) Testing

Reference device: wco2-idf5-asw-01 (10.150.0.21, AOS-CX 6100, SNMPv3 `fpsrw`).

### 16.1 Discovery & Detection
- [ ] Active scan finds the AOS-CX switch
- [ ] Platform detected as `aos_cx` (not `other`/unknown) — sysDescr "HPE ANW …"
      → aos_cx; sysObjectID 47196.* → aruba/aos_cx
- [ ] Approve → enrichment runs
- [ ] model populated from sysDescr ("R9Y04A 6100 48G CL4 4SFP+ Sw")
- [ ] os_version populated from sysDescr firmware token ("PL.10.16.1030")
- [ ] serial populated via SNMP walk (entPhysicalSerialNum at non-standard index)

### 16.2 SNMP Verification (from the api container)
```bash
docker compose exec api python3 - <<'PY'
import asyncio
from pysnmp.hlapi.v3arch.asyncio import *
async def t():
    e=SnmpEngine(); u=UsmUserData('fpsrw', <auth_key>, <priv_key>,
        authProtocol=usmHMACSHAAuthProtocol, privProtocol=usmAesCfb128Protocol)
    tgt=await UdpTransportTarget.create(('10.150.0.21',161),timeout=4)
    ei,es,_,vb=await get_cmd(e,u,tgt,ContextData(),
        ObjectType(ObjectIdentity('1.3.6.1.2.1.1.1.0')))   # sysDescr
    print(ei or [str(v[1]) for v in vb]); e.close_dispatcher()
asyncio.run(t())
PY
```
- [ ] sysDescr returns the "HPE ANW …" format
- [ ] sysObjectID returns 47196.*
- [ ] entPhysicalSerialNum walk (1.3.6.1.2.1.47.1.1.1.1.11) returns the serial

### 16.3 Environment Telemetry (SNMP only — no gNMI on the 6100)
- [ ] SNMP polling active
- [ ] `telemetry` measurement has cpu_pct (hrProcessorLoad avg)
- [ ] `telemetry` has memory_used_pct / memory_total_bytes (hrStorage idx 1)
- [ ] `telemetry` has temp_max_c, fan_count, psu_count
- [ ] `device_environment` has a temperature_c point per sensor (tag sensor_name)
- [ ] uptime collecting; interface traffic collecting
- [ ] No REST API errors in the logs (REST is not used for aos_cx)

### 16.4 Temperature Alerts
- [ ] "High Temperature Warning" / "Critical" / "Sensor Failed" rules seeded
      (Settings → Alerting), is_system, toggleable
- [ ] Crossing thresholds fires an AlertEvent (warn ≥75°C, crit ≥85°C)

### 16.5 Known Limitations (6100)
- REST API not supported (login 400/401) — SNMP only
- Per-unit fan RPM unavailable (rpm sensors read -1); fan/PSU is presence/count
  only (no standard per-unit oper-status; entPhysical `.8` is HardwareRev)
- SSH banner is generic OpenSSH (no platform hint)
- "Wrong SNMP PDU digest" means the stored SNMPv3 auth/priv key doesn't match
  the device — fix the credential in Settings → Credentials (not a code bug).
  The poller uses a fresh engine per poll for general robustness.

---

## Test Results Log

| Date | Tester | Version | Pass | Fail | Notes |
|------|--------|---------|------|------|-------|
|      |        |         |      |      |       |

## Known Issues / Skip Conditions

- FortiOS interface discovery requires trusted-host config for the container IP range
- Config push requires ALLOW_CONFIG_PUSH=true
- ICMP checks require NET_RAW (configured on check-engine/api)
- gNMI streaming requires subscriptions applied to devices (telemetry config wizard)
- Email alerts require SMTP config in .env
- A device that blocks SSH from the collector is still marked reachable via the
  TCP/443 fallback (reachability monitor)
- AOS-CX 6100: SNMP only (no REST, no gNMI); fan/PSU reported as presence/count
  (no per-unit RPM or oper-status on this model)

---

*Testing Plan v1.0 — maintain as features are added.*
