param(
  [string]$MediaGoPath = ""
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$StartUi = Join-Path $ProjectRoot "scripts\start-ui.ps1"

if ([string]::IsNullOrWhiteSpace($MediaGoPath)) {
  throw "Missing -MediaGoPath. Pass the explicit MediaGo client executable file path; this launcher has no default path, no prompt and no discovery."
}

if (-not (Test-Path -LiteralPath $StartUi)) {
  throw "Missing backend launcher: $StartUi"
}
if (-not (Test-Path -LiteralPath $MediaGoPath -PathType Leaf)) {
  throw "Missing MediaGo executable file: $MediaGoPath"
}

$MediaGoExe = (Resolve-Path -LiteralPath $MediaGoPath).ProviderPath

powershell.exe -NoProfile -File $StartUi -NoBrowser
if ($LASTEXITCODE -ne 0) { throw "VDO startup failed; MediaGo was not started by this launcher." }

$running = Get-Process -ErrorAction SilentlyContinue |
  Where-Object { $_.Path -eq $MediaGoExe } |
  Select-Object -First 1

if ($null -eq $running) {
  Start-Process -FilePath $MediaGoExe
  Write-Host "MediaGo client started: $MediaGoExe"
} else {
  Write-Host "MediaGo client is already running. PID: $($running.Id)"
}

Write-Host "Smart import backend: http://127.0.0.1:8921/"
