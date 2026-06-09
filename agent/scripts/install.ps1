# NetPulse Agent Windows installer. Run as Administrator:
#   powershell -ExecutionPolicy Bypass -File install.ps1 `
#     -Server https://<server> -Token <TOKEN>
param(
    [Parameter(Mandatory = $true)][string]$Server,
    [Parameter(Mandatory = $true)][string]$Token,
    [string]$InstallDir = "C:\Program Files\NetPulse",
    [string]$ConfigDir  = "C:\ProgramData\NetPulse"
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
Invoke-WebRequest -Uri $DownloadUrl -OutFile $BinaryPath -UseBasicParsing

Write-Host "Enrolling agent..."
& $BinaryPath --enroll $Token --server $Server --config $ConfigPath
if ($LASTEXITCODE -ne 0) { Write-Error "Enrollment failed!"; exit 1 }

Write-Host "Installing Windows service..."
& $BinaryPath --install-service --config $ConfigPath

Start-Service -Name "NetPulseAgent"
Write-Host "NetPulse Agent installed." -ForegroundColor Green
Get-Service -Name "NetPulseAgent" | Select-Object Name, Status, StartType
