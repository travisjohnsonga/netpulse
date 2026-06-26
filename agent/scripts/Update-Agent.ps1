<#
.SYNOPSIS
  Update the NetPulse agent on this Windows host.

.DESCRIPTION
  Modes (most operators need NO arguments - see the third form):
    1. Download from a server:  .\Update-Agent.ps1 -Server https://<server> [-Insecure]
    2. Swap from a local binary: .\Update-Agent.ps1 -Binary C:\path\to\netpulse-agent-windows-amd64.exe
    3. No args (the common case): .\Update-Agent.ps1
       reads server_url (and insecure_tls) from the enrolled config.json, so a
       host updates "from wherever it enrolled" with nothing to type.

  Run from an ELEVATED (Administrator) PowerShell. Arg style + paths mirror
  install.ps1 (-Server / -Insecure, C:\Program Files\NetPulse +
  C:\ProgramData\NetPulse); the download path is the same the installer uses
  ({server}/agent/download/windows-amd64).

  Safety features (the whole point - keep these intact):
    - Verifies the NEW binary runs + reports a version BEFORE replacing the
      running one.
    - Backs up the current binary so a bad update can be rolled back.
    - Confirms the service comes back up AND reports the new version; auto-rolls
      back on failure.
    - Uses curl.exe (HTTP/1.1) for the download - Invoke-WebRequest on PS5.1
      fails against the server's HTTP/2 + the self-signed TLS, the documented
      download gotcha.
    - Handles the "service doesn't exist yet" case (installs it) vs "exists"
      (swap in place).
#>
[CmdletBinding()]
# Write-Host is intentional: this is an interactive installer whose console
# output IS the UX (matches install.ps1). Suppress the analyzer's Write-Host rule
# script-wide rather than route status text through the object pipeline.
[Diagnostics.CodeAnalysis.SuppressMessageAttribute('PSAvoidUsingWriteHost', '',
  Justification = 'Interactive installer console output')]
param(
  [string]$Server,
  [string]$Binary,
  [switch]$Insecure,
  [string]$InstallDir = "C:\Program Files\NetPulse",
  [string]$ConfigDir  = "C:\ProgramData\NetPulse"
)

$ErrorActionPreference = "Stop"
$ServiceName = "NetPulseAgent"
$BinPath     = Join-Path $InstallDir "netpulse-agent.exe"
$BackupPath  = "$BinPath.bak"
$ConfigPath  = Join-Path $ConfigDir "config.json"

# ---- elevation check ----
$isAdmin = ([Security.Principal.WindowsPrincipal] `
  [Security.Principal.WindowsIdentity]::GetCurrent()
).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
  Write-Error "Must run from an elevated (Administrator) PowerShell - it stops/starts the service and writes Program Files."
  exit 1
}

# ---- default the server (and self-signed flag) from the enrolled config ----
# "update from wherever I'm enrolled" - so a local run needs no arguments.
if (-not $Server -and -not $Binary -and (Test-Path $ConfigPath)) {
  try {
    $cfg = Get-Content $ConfigPath -Raw | ConvertFrom-Json
    if ($cfg.server_url) {
      $Server = $cfg.server_url
      Write-Host "No -Server given; using server_url from ${ConfigPath}: $Server"
      if (-not $Insecure -and $cfg.insecure_tls) {
        $Insecure = $true
        Write-Host "  (config has insecure_tls=true -> downloading with -Insecure)"
      }
    }
  } catch {
    Write-Warning "Could not read ${ConfigPath}: $($_.Exception.Message)"
  }
}

if (-not $Server -and -not $Binary) {
  Write-Error "No -Server, no -Binary, and no server_url in ${ConfigPath}.`n  .\Update-Agent.ps1`n  .\Update-Agent.ps1 -Server https://<server> [-Insecure]`n  .\Update-Agent.ps1 -Binary C:\path\to\netpulse-agent-windows-amd64.exe"
  exit 1
}

function Get-AgentVersion([string]$exe) {
  if (-not (Test-Path $exe)) { return "(none)" }
  try { (& $exe --version 2>&1 | Select-Object -Last 1) -replace '.*\s(\S+)$', '$1' }
  catch { "(unreadable)" }
}

# ---- current version ----
$currentVer = Get-AgentVersion $BinPath
Write-Host "Current installed version: $currentVer"

# ---- obtain the new binary into a temp file ----
$tmpBin = Join-Path $env:TEMP ("netpulse-agent-{0}.exe" -f ([guid]::NewGuid().ToString('N')))

try {
  if ($Binary) {
    Write-Host "Using local binary: $Binary"
    if (-not (Test-Path $Binary)) { Write-Error "$Binary not found."; exit 1 }
    Copy-Item $Binary $tmpBin -Force
  }
  else {
    $dlUrl = "$($Server.TrimEnd('/'))/agent/download/windows-amd64"
    Write-Host "Downloading: $dlUrl"
    # curl.exe (built into Win10/Server2019+): -f fail on HTTP error, -L follow
    # redirects, -k allow self-signed. NOT Invoke-WebRequest (HTTP/2 +
    # self-signed breaks it on PS5.1).
    $curlArgs = @("-fL", "-o", $tmpBin, $dlUrl)
    if ($Insecure) { $curlArgs = @("-k") + $curlArgs }
    & curl.exe @curlArgs
    if ($LASTEXITCODE -ne 0) { Write-Error "Download failed (curl.exe exit $LASTEXITCODE)."; exit 1 }
  }

  # ---- VERIFY the new binary BEFORE touching the running one ----
  $newVer = Get-AgentVersion $tmpBin
  if ($newVer -in @("(none)", "(unreadable)")) {
    Write-Error "The downloaded/provided binary won't run (--version failed). Aborting - running agent untouched."
    exit 1
  }
  Write-Host "New binary version:        $newVer"
  if ($newVer -eq $currentVer) { Write-Host "NOTE: new version ($newVer) == current ($currentVer). Re-applying anyway." }

  # ---- does the service exist? ----
  $svc = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue

  if ($svc) {
    Write-Host "Stopping $ServiceName..."
    Stop-Service -Name $ServiceName -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 1
  } else {
    Write-Host "Service $ServiceName not found - will install it after placing the binary."
  }

  # ---- back up + swap ----
  if (Test-Path $BinPath) {
    Write-Host "Backing up current binary -> $BackupPath"
    Copy-Item $BinPath $BackupPath -Force
  }
  if (-not (Test-Path $InstallDir)) { New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null }
  Write-Host "Installing new binary -> $BinPath"
  Copy-Item $tmpBin $BinPath -Force

  # ---- start (or install) + confirm ----
  if (-not $svc) {
    if (-not (Test-Path $ConfigPath)) {
      Write-Error "No service AND no config at $ConfigPath - this host isn't enrolled. Run the one-paste install (Settings -> Agents) instead."
      exit 1
    }
    Write-Host "Installing the service from the new binary..."
    & $BinPath --install-service --config $ConfigPath
  }

  Write-Host "Starting $ServiceName..."
  Start-Service -Name $ServiceName
  Start-Sleep -Seconds 2

  $svcNow = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
  if ($svcNow -and $svcNow.Status -eq "Running") {
    $runningVer = Get-AgentVersion $BinPath
    Write-Host ""
    Write-Host "[OK] Update complete. $ServiceName is Running, reporting version: $runningVer"
    Write-Host "     (was $currentVer -> now $runningVer)"
    if (Test-Path $BackupPath) { Write-Host "     Previous binary backed up at $BackupPath (remove when satisfied)." }
  }
  else {
    Write-Host ""
    Write-Warning "$ServiceName did NOT come back up after the swap. Rolling back..."
    if (Test-Path $BackupPath) {
      Copy-Item $BackupPath $BinPath -Force
      Start-Service -Name $ServiceName -ErrorAction SilentlyContinue
      Write-Warning "Rolled back to the previous binary ($currentVer). Investigate before retrying."
    } else {
      Write-Warning "No backup to roll back to. Check: Get-Service $ServiceName ; Get-EventLog -LogName Application -Source $ServiceName -Newest 10"
    }
    exit 1
  }
}
finally {
  if (Test-Path $tmpBin) { Remove-Item $tmpBin -Force -ErrorAction SilentlyContinue }
}
