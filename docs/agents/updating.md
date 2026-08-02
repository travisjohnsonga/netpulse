# Updating the Agent

Updating an enrolled agent means **replacing its binary** with a newer build and
restarting the service. Enrollment, the host's certificate, and `config.json` are
left untouched — an update is binary-only.

There are two supported paths. Both use the **same safe updater** under the hood
(`agent/scripts/update-agent.sh` / `Update-Agent.ps1`), which:

- **verifies the new binary runs and reports a version *before* replacing the
  running one** (catches a stale, wrong, or corrupt download);
- **backs up** the current binary alongside it (`.bak`);
- **confirms the service comes back up** and reports the new version after the
  swap — and **auto-rolls-back** to the backup if it does not;
- handles the "service doesn't exist yet" case by installing it.

!!! note "Always use the `https://` server URL"
    Same as install: nginx redirects `http → https`. For a self-signed server
    certificate add `--insecure` (Linux) / `-Insecure` (Windows). The updater
    also reads `insecure_tls` from the host's `config.json`, so a no-arg local
    run inherits the setting from enrollment.

## Path 1 — the pre-placed local updater (no arguments)

The installer leaves a persistent updater on the host so later updates need
nothing typed. It reads `server_url` (and `insecure_tls`) from the enrolled
`config.json` — i.e. it updates **from wherever the host enrolled**.

=== "Linux"

    ```bash
    sudo /usr/local/bin/netpulse-agent-update.sh
    ```

=== "Windows (elevated PowerShell)"

    ```powershell
    & 'C:\Program Files\NetPulse\Update-Agent.ps1'
    ```

!!! warning "Why the script lives in a protected directory"
    The local updater is placed in the **root-owned** install dir
    (`/usr/local/bin` on Linux, `C:\Program Files\NetPulse` on Windows), **not**
    `/tmp` / `$env:TEMP`. It runs **elevated** and swaps the privileged agent
    binary, so it must live somewhere only an administrator/root can write. A
    user-writable copy would be a privilege-escalation vector — a low-privilege
    user could edit a script an admin later runs elevated. (The transient
    run-once install/update *bootstrap* below correctly stays in TEMP — it runs
    once and isn't left on the box.)

## Path 2 — pull-from-server one-paste

Mirrors the install one-liner, fetching the updater from the server and running
it. Use this if the local copy is missing, or to update from a *different*
server than the one in `config.json`. The exact command (with the right server
URL and self-signed flag) is shown in **Settings → Agents → Generate Token**
beside the install command.

=== "Linux"

    ```bash
    curl -fsSL https://<server>/agent/update | sudo bash -s -- --server https://<server> [--insecure]
    ```

=== "Windows (elevated PowerShell)"

    ```powershell
    curl.exe -fL -o "$env:TEMP\update.ps1" "https://<server>/agent/update.ps1"
    powershell -ExecutionPolicy Bypass -File "$env:TEMP\update.ps1" -Server "https://<server>" [-Insecure]
    ```

    Windows uses `curl.exe` (built into Windows 10 / Server 2019+), **not**
    `Invoke-WebRequest`: stock PowerShell 5.1 can't speak HTTP/2 and fails
    against the nginx front door.

    The updater's pre-swap version check is also hardened for **PowerShell 5.1**:
    `--version` output arriving on stderr / as an `ErrorRecord` is coerced to a
    string and matched against `netpulse-agent vX.Y.Z`, so the check no longer
    misreports `(unreadable)` and skip the upgrade.

## Updating from a local binary (air-gapped)

If you've already copied a binary to the host (e.g. from the CI artifact), swap
it in directly — the same verify/backup/rollback safety applies:

=== "Linux"

    ```bash
    sudo /usr/local/bin/netpulse-agent-update.sh --binary /path/to/netpulse-agent-linux-amd64
    ```

=== "Windows (elevated PowerShell)"

    ```powershell
    & 'C:\Program Files\NetPulse\Update-Agent.ps1' -Binary C:\path\to\netpulse-agent-windows-amd64.exe
    ```

## Where the binary comes from

The updater downloads from the **same** path the installer uses —
`{server}/agent/download/linux-<arch>` or `/agent/download/windows-amd64` — which
serves the binary from the server's `agent/dist/` mount, falling back to the
public GitHub release. So "update" tracks whatever the server is currently
serving.

## Verifying the result

```bash
# Linux
/usr/local/bin/netpulse-agent --version
systemctl status netpulse-agent
```

```powershell
# Windows
& 'C:\Program Files\NetPulse\netpulse-agent.exe' --version
Get-Service NetPulseAgent
```

The updater also prints `was <old> → now <new>` on success.

## Roadmap

This is a **manual, operator-driven** update. Fully automatic agent
self-updating is roadmapped but deliberately gated behind strict security
controls (signed releases, staged rollout) — see
[Agent auto-update](../roadmap.md#agent-auto-update-arc-gated-post-evaluation-strict-security-controls)
and the
[“agents needing update” fleet view](../roadmap.md#fleet-agents-needing-update-view-near-term)
that precedes it.
