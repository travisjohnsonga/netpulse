# NetPulse Agent Windows installer. Run as Administrator. Always use the https://
# server URL (nginx redirects http→https). Add -Insecure for a self-signed cert.
#   powershell -ExecutionPolicy Bypass -File install.ps1 `
#     -Server https://<server> -Token <TOKEN> [-Insecure]
param(
    [Parameter(Mandatory = $true)][string]$Server,
    [Parameter(Mandatory = $true)][string]$Token,
    [string]$InstallDir = "C:\Program Files\NetPulse",
    [string]$ConfigDir  = "C:\ProgramData\NetPulse",
    [switch]$Insecure
)
$ErrorActionPreference = "Stop"

Write-Host "Installing NetPulse Agent..."
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
New-Item -ItemType Directory -Force -Path $ConfigDir  | Out-Null

$BinaryPath = Join-Path $InstallDir "netpulse-agent.exe"
$ConfigPath = Join-Path $ConfigDir "config.json"
$DownloadUrl = "$Server/agent/download/windows-amd64"

Write-Host "Downloading agent binary..."
# Use curl.exe (built into Windows 10 / Server 2019+), NOT Invoke-WebRequest:
# PowerShell 5.1's .NET HttpWebRequest can't speak HTTP/2 and fails against the
# HTTP/2 nginx front door ("connection closed on send"); curl.exe uses HTTP/1.1.
#   -f  fail on an HTTP error (so a 404 page is NOT saved as the "binary")
#   -L  follow redirects (/agent/download/windows-amd64 → the GitHub release)
#   -k  skip cert validation for a self-signed server (only when -Insecure)
# --insecure is also passed to enrollment so the agent's own TLS skips validation.
$EnrollOpts = @()
$CurlArgs = @("-f", "-L", "-o", $BinaryPath, $DownloadUrl)
if ($Insecure) {
    $EnrollOpts += "--insecure"
    $CurlArgs = @("-k") + $CurlArgs
}
& curl.exe @CurlArgs
if ($LASTEXITCODE -ne 0) { Write-Error "Binary download failed (curl.exe exit $LASTEXITCODE)."; exit 1 }

Write-Host "Enrolling agent..."
& $BinaryPath --enroll $Token --server $Server --config $ConfigPath @EnrollOpts
if ($LASTEXITCODE -ne 0) { Write-Error "Enrollment failed!"; exit 1 }

Write-Host "Installing Windows service..."
& $BinaryPath --install-service --config $ConfigPath
if ($LASTEXITCODE -ne 0) { Write-Error "Service install failed!"; exit 1 }

# --install-service already starts the service; this is a harmless no-op if it's
# already running, and a clear failure if registration didn't take.
Start-Service -Name "NetPulseAgent" -ErrorAction SilentlyContinue

# Leave a persistent updater so the host can be updated later with a single
# no-arg command (it reads server_url from config.json). SECURITY: it lands in
# $InstallDir (Program Files, admin-write-only) — NOT $env:TEMP — because it runs
# elevated and swaps the agent binary; a user-writable copy would be a privesc
# vector. Best-effort: a fetch failure doesn't fail the install.
$UpdateScriptPath = Join-Path $InstallDir "Update-Agent.ps1"
$UpdUrl = "$Server/agent/update.ps1"
$UpdArgs = @("-fL", "-o", $UpdateScriptPath, $UpdUrl)
if ($Insecure) { $UpdArgs = @("-k") + $UpdArgs }
& curl.exe @UpdArgs
if ($LASTEXITCODE -eq 0) {
    Write-Host "Update later with (elevated PowerShell): & '$UpdateScriptPath'"
} else {
    Write-Warning "Could not fetch the updater; re-pull later from $UpdUrl"
}

Write-Host "NetPulse Agent installed." -ForegroundColor Green
Get-Service -Name "NetPulseAgent" | Select-Object Name, Status, StartType
