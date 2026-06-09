# Wireless Access Point Monitoring

NetPulse collects wireless telemetry from UniFi controllers via the local REST API.

## Metrics Collected

### Per-AP Health

| Metric | Description | Good | Warning | Critical |
|--------|-------------|------|---------|----------|
| CPU % | Processor utilization | <50% | 50-80% | >80% |
| Memory % | RAM utilization | <70% | 70-85% | >85% |
| Temperature | Device temp (°C) | <60°C | 60-75°C | >75°C |
| Uptime | Time since last reboot | >7d | 1-7d | <1d |

### Per-Radio (2.4GHz / 5GHz / 6GHz)

| Metric | Description | Good | Warning | Critical |
|--------|-------------|------|---------|----------|
| Channel Utilization % | % of airtime in use | <50% | 50-75% | >75% |
| Noise Floor (dBm) | Background RF noise | >-90 | -90 to -85 | <-85 |
| TX Power (dBm) | Transmit power | — | — | — |
| Retry Rate % | % of frames retransmitted | <5% | 5-15% | >15% |
| Client Count | Connected clients per radio | <25 | 25-40 | >40 |

### UniFi Satisfaction Score (0-100)

UniFi calculates a composite score per AP and per connected client based on:

- **SNR** (Signal-to-Noise Ratio) — primary factor. Higher SNR = better signal quality.
  Good: >25dB, Warning: 15-25dB, Poor: <15dB
- **PHY Rate** — negotiated data rate between client and AP. Higher = better.
  Affected by distance, interference, antenna.
- **Retry Rate** — % of frames that required retransmission. High retries indicate
  interference or weak signal.
  Good: <5%, Warning: 5-15%, Poor: >15%
- **Latency** — round-trip time from client through AP to gateway.
  Good: <10ms, Warning: 10-50ms, Poor: >50ms
- **Connection Stability** — frequency of disconnects and reassociations.

Score interpretation:

| Score | Status | Meaning |
|-------|--------|---------|
| 90-100 | 🟢 Excellent | Users experience great performance |
| 70-89 | 🟡 Good | Minor issues, mostly unnoticeable |
| 50-69 | 🟠 Fair | Users may notice slowness |
| <50 | 🔴 Poor | Users experiencing problems |

> **Note:** The satisfaction score is calculated by the UniFi controller,
> not by NetPulse. NetPulse displays the score as reported by the controller.

## Channel Utilization

Channel utilization measures how busy the wireless medium is. High utilization means
less airtime available for clients.

**What causes high channel utilization:**

- Too many clients on one AP/channel
- Neighboring APs on the same channel (co-channel interference)
- Non-WiFi interference (microwave, Bluetooth)
- Legacy 802.11b/g clients slowing the medium
- Large file transfers from a few clients

**Thresholds:**

- <50%: Healthy, plenty of airtime available
- 50-75%: Degraded, clients may notice
- >75%: Congested, significant impact on all clients

**2.4GHz vs 5GHz:**

- 2.4GHz has only 3 non-overlapping channels (1, 6, 11) — much more prone to congestion
- 5GHz has 20+ channels — much more capacity
- 6GHz (WiFi 6E/7) has 59 channels — best

## Noise Floor

The noise floor is the level of background RF energy on a channel (in dBm — negative
values, higher is better).

**Typical values:**

- -95 dBm: Very clean, excellent
- -90 dBm: Clean, good
- -85 dBm: Some interference, acceptable
- -80 dBm: Noisy, performance impacted
- <-75 dBm: Very noisy, significant issues

**Common sources of noise:**

- Neighboring WiFi networks
- Bluetooth devices
- Microwave ovens (2.4GHz)
- Baby monitors, wireless cameras
- Radar (5GHz DFS channels)

## Retry Rate

The retry rate is the percentage of wireless frames that had to be retransmitted
because the receiver didn't acknowledge them.

**High retries indicate:**

- Weak signal (client too far from AP)
- High interference (co-channel or adjacent)
- Obstructions between client and AP
- Outdated/incompatible client drivers

**Target:** <5% retry rate
**Acceptable:** 5-10%
**Problem:** >15% — investigate signal/interference

## Client Count Guidelines

These are general guidelines — actual limits depend on traffic type and AP model.

| AP Tier | Max Clients (2.4GHz) | Max Clients (5GHz) |
|---------|---------------------|-------------------|
| Entry (U6-Lite) | 15-20 | 25-30 |
| Mid (U6-Pro) | 20-30 | 40-60 |
| High-density (U6-Enterprise) | 30-40 | 75-100 |

> **Note:** These are practical limits for good performance, not hard limits.
> Video conferencing and VoIP are much more sensitive to AP load than web browsing.

## Alerts

NetPulse can alert on:

- AP offline / disconnected
- Satisfaction score below threshold
- Channel utilization above threshold
- Client count above threshold
- High retry rate
- Noise floor degradation

Configure alert thresholds in:
**Settings → Alerting → Alert Rules**

## WAN Monitoring (UDM/Gateway)

For UniFi Dream Machine and other gateway devices, NetPulse also collects:

| Metric | Description |
|--------|-------------|
| WAN Latency | Round-trip to gateway (ms) |
| WAN Throughput | Current rx/tx rate (bps) |
| WAN Uptime | Time WAN interface has been up |
| Dual WAN status | Active/standby/load-balanced |
| Adopted devices | Count of adopted UniFi devices |
| Disconnected devices | Count of disconnected devices |

## Limitations

- UniFi APs do not expose SNMP directly (monitoring requires a UniFi controller)
- The satisfaction score algorithm is proprietary to Ubiquiti and not fully documented
- Historical data retention depends on NetPulse InfluxDB retention settings
  (default: 90 days)
- Some metrics (temperature) are only available on certain AP models

## Setting Up

See [UniFi Integration](unifi.md) for controller setup instructions.
