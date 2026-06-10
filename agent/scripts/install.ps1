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
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
# For a self-signed server: skip cert validation on the binary download and pass
# --insecure to enrollment.
$EnrollOpts = @()
$IwrOpts = @{}
if ($Insecure) {
    $EnrollOpts += "--insecure"
    if ($PSVersionTable.PSVersion.Major -ge 6) { $IwrOpts["SkipCertificateCheck"] = $true }
    else { [Net.ServicePointManager]::ServerCertificateValidationCallback = { $true } }
}
Invoke-WebRequest -Uri $DownloadUrl -OutFile $BinaryPath -UseBasicParsing @IwrOpts

Write-Host "Enrolling agent..."
& $BinaryPath --enroll $Token --server $Server --config $ConfigPath @EnrollOpts
if ($LASTEXITCODE -ne 0) { Write-Error "Enrollment failed!"; exit 1 }

Write-Host "Installing Windows service..."
& $BinaryPath --install-service --config $ConfigPath

Start-Service -Name "NetPulseAgent"
Write-Host "NetPulse Agent installed." -ForegroundColor Green
Get-Service -Name "NetPulseAgent" | Select-Object Name, Status, StartType
