# Agent Installation

## Prerequisites

- A running, reachable NetPulse server (HTTPS)
- An enrollment token (Settings → Agents → Generate Token)
- Outbound HTTPS (port 443) from the server to NetPulse

!!! note "Always use the `https://` server URL"
    nginx redirects `http → https`, and a redirected POST would drop the
    enrollment body. For a self-signed server certificate, add `--insecure`
    (Linux) / `-Insecure` (Windows), which the modal can append for you.

## Generating an enrollment token

1. Go to **Settings → Agents**
2. Click **Generate Token**
3. Choose the **Target OS** (Linux / Windows / Both), expiry, and max uses
4. (If the server cert is self-signed) tick **Server uses self-signed certificate**
5. Copy the install command shown for your platform

## Linux

One-line install:

```bash
curl -fsSL https://your-netpulse/agent/install | sudo bash -s -- \
  --server https://your-netpulse \
  --token YOUR_TOKEN
```

!!! success "Verified end-to-end"
    This one-liner is verified working: nginx proxies `/agent/*` to the API,
    which serves `install.sh` and the platform binary from `AGENT_DIR`. It
    downloads the binary, enrolls (mTLS cert issued), and installs + starts a
    hardened `netpulse-agent` systemd unit in one step. Re-running it is safe
    (see [Re-enrollment / upgrade](#re-enrollment-upgrade)).

Self-signed server certificate:

```bash
curl -fsSL -k https://your-netpulse/agent/install | sudo bash -s -- \
  --server https://your-netpulse \
  --token YOUR_TOKEN \
  --insecure
```

Manual install:

```bash
# Download the binary (amd64 or arm64 — match your server)
sudo curl -fsSL https://your-netpulse/agent/download/linux-amd64 \
  -o /usr/local/bin/netpulse-agent
sudo chmod +x /usr/local/bin/netpulse-agent

# Enroll (writes config + cert/key/ca under /etc/netpulse-agent)
sudo netpulse-agent --enroll YOUR_TOKEN --server https://your-netpulse \
  --config /etc/netpulse-agent/config.json   # add --insecure for self-signed

# Start the service
sudo systemctl enable --now netpulse-agent
```

Supported architectures: `linux/amd64`, `linux/arm64` (auto-detected by the
installer).

## Windows

!!! warning "`/agent/install.ps1` is not served yet"
    The one-liner below fetches `install.ps1` from the server, but that endpoint
    is **not wired up yet** (only `/agent/install` and `/agent/download/<platform>`
    are routed). Until it is, install Windows agents manually: download
    `windows-amd64` from `/agent/download/windows-amd64`, then run
    `netpulse-agent.exe --enroll <TOKEN> --server <URL> --config <path>` followed
    by `--install-service`.

Run PowerShell **as Administrator**:

```powershell
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
Invoke-WebRequest -Uri "https://your-netpulse/agent/install.ps1" `
  -OutFile "$env:TEMP\install.ps1"
powershell -ExecutionPolicy Bypass -File "$env:TEMP\install.ps1" `
  -Server "https://your-netpulse" `
  -Token "YOUR_TOKEN"        # add -Insecure for a self-signed server cert
```

Supported architecture: `windows/amd64`.

## Verifying the installation

```bash
# Linux
sudo systemctl status netpulse-agent
journalctl -u netpulse-agent -f
```

Then confirm the server appears under **Servers → All Servers** (and the agent
under **Settings → Agents**). First metrics arrive within one collection
interval (default 30s).

## Re-enrollment / upgrade

Re-running the installer on a host that already has an agent is safe — it stops
the running service, replaces the binary, and **re-enrolls in place**: the
server reuses the existing agent record and rotates its certificate (the agent
keeps its identity, device link, and role assignments). Just run the one-liner
again:

```bash
curl -fsSL https://your-netpulse/agent/install | sudo bash -s -- \
  --server https://your-netpulse --token YOUR_TOKEN
```

If the host was **revoked** in the UI, re-enrolling creates a fresh agent record
instead of resurrecting the revoked one. In the rare case the server can't
reconcile the host to a single record, enrollment returns **HTTP 409** and the
installer prints a clear message — revoke the stale agent first:

> **Settings → Agents → [hostname] → Revoke**, then re-run the installer.
