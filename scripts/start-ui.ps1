param(
  [switch]$NoBrowser,
  [switch]$UpdatePortRecord,
  [string]$PortRecordPath = ""
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
. (Join-Path $ProjectRoot "scripts\runtime-python.ps1")
$ServiceName = "video-download-orchestrator Web UI"
$ConfigPath = Join-Path $ProjectRoot "config\video_orchestrator.json"

if (-not (Test-Path -LiteralPath $ConfigPath)) {
  throw "Missing runtime config: $ConfigPath"
}

$Config = Get-Content -LiteralPath $ConfigPath -Raw | ConvertFrom-Json
$PreferredPort = [int]$Config.web.port
$HostAddress = [string]$Config.web.host
$Scheme = [string]$Config.web.scheme
$StateDirValue = [string]$Config.runtime.state_dir
$metaDir = if ([System.IO.Path]::IsPathRooted($StateDirValue)) {
  [System.IO.Path]::GetFullPath($StateDirValue)
} else {
  [System.IO.Path]::GetFullPath((Join-Path $ProjectRoot $StateDirValue))
}
$LocalAllowlistPath = Join-Path $ProjectRoot ".vdo\openclaw-telegram-allowlist.txt"

function Test-PortListening {
  param([int]$Port)
  $client = [System.Net.Sockets.TcpClient]::new()
  try {
    $connectTask = $client.ConnectAsync($HostAddress, $Port)
    if (-not $connectTask.Wait(1000)) {
      return $false
    }
    return $client.Connected
  } catch {
    return $false
  } finally {
    $client.Dispose()
  }
}

function Test-ApiHealthy {
  param([int]$Port)
  try {
    $health = Invoke-RestMethod -Uri "${Scheme}://${HostAddress}:$Port/health" -Method Get -TimeoutSec 2
    return $health.status -eq "ok" -and $health.service -eq "video-download-orchestrator"
  } catch {
    return $false
  }
}

function Add-PortRecord {
  param(
    [int]$Port,
    [int]$ProcessId,
    [string]$Url,
    [string]$Status,
    [string]$RecordPath = ""
  )
  if ([string]::IsNullOrWhiteSpace($RecordPath) -or -not (Test-Path -LiteralPath $RecordPath)) {
    return
  }
  $stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
  $entry = @"

### $stamp | PowerShell launcher (operator-triggered)
- Service: $ServiceName
- URL: $Url
- Port: $Port
- PID: $ProcessId
- Status: $Status
- Workspace: $ProjectRoot
- Notes: Started by one-click launcher; bound to $HostAddress.
"@
  Add-Content -LiteralPath $RecordPath -Value $entry -Encoding UTF8
}

Set-Location $ProjectRoot

$env:PYTHONPATH = Join-Path $ProjectRoot "src"
& $VdoPython scripts\sync_runtime_config.py
if ($LASTEXITCODE -ne 0) { throw "Runtime config generation failed." }
& $VdoPython -m video_orchestrator.config check | Out-Host
if ($LASTEXITCODE -ne 0) { throw "Runtime config check failed." }

$port = $PreferredPort
if (Test-ApiHealthy -Port $port) {
  $entrypointStatus = (& $VdoPython -m video_orchestrator.cli entrypoint-status --format json) | ConvertFrom-Json
  if ($entrypointStatus.service_lifecycle.status -ne "managed_running") {
    throw "A VDO-compatible service is healthy on port $port but is not owned by a valid managed receipt."
  }
  $url = "${Scheme}://${HostAddress}:$port/"
  Write-Host "$ServiceName is already listening at $url"
  if (-not $NoBrowser) {
    Start-Process $url
  }
  exit 0
}
if (Test-PortListening -Port $port) {
  throw "Configured port $port is occupied by a service that did not pass the VDO health check."
}

$port = $PreferredPort
$url = "${Scheme}://${HostAddress}:$port/"

$pythonPath = Join-Path $ProjectRoot "src"
$env:PYTHONPATH = $pythonPath
New-Item -ItemType Directory -Force -Path $metaDir | Out-Null
if (-not $env:VDO_TELEGRAM_ALLOWED_USER_IDS -and (Test-Path -LiteralPath $LocalAllowlistPath)) {
  $allowedActorIds = Get-Content -LiteralPath $LocalAllowlistPath |
    ForEach-Object { $_.Trim() } |
    Where-Object { $_ -and -not $_.StartsWith("#") }
  if ($allowedActorIds) {
    $env:VDO_TELEGRAM_ALLOWED_USER_IDS = ($allowedActorIds -join ",")
  }
}
$stdoutLog = Join-Path $metaDir "ui-server.stdout.log"
$stderrLog = Join-Path $metaDir "ui-server.stderr.log"
$ReceiptId = [guid]::NewGuid().ToString()

$process = Start-Process `
  -FilePath $VdoPython `
  -ArgumentList @("-m", "video_orchestrator.web_server", "--vdo-project-root", $ProjectRoot, "--vdo-receipt-id", $ReceiptId) `
  -WorkingDirectory $ProjectRoot `
  -WindowStyle Hidden `
  -RedirectStandardOutput $stdoutLog `
  -RedirectStandardError $stderrLog `
  -PassThru

$metaPath = Join-Path $metaDir "ui-server.json"
$metadata = [ordered]@{
  contract = "vdo_ui_service_lifecycle_v1"
  receipt_id = $ReceiptId
  service = $ServiceName
  status = "starting"
  url = $url
  host = $HostAddress
  port = $port
  config_path = $ConfigPath
  pid = $process.Id
  process_start_time_utc = $process.StartTime.ToUniversalTime().ToString("o")
  process_path = $process.Path
  started_at = (Get-Date).ToString("s")
  stopped_at = $null
  stop_reason = $null
  project_root = $ProjectRoot
  stdout_log = $stdoutLog
  stderr_log = $stderrLog
  status_command = "powershell.exe -NoProfile -File scripts\status-ui.ps1"
  stop_command = "powershell.exe -NoProfile -File scripts\stop-ui.ps1 -ConfirmStop"
}
$metadata | ConvertTo-Json | Set-Content -LiteralPath $metaPath -Encoding UTF8

$healthy = $false
$startupDeadline = (Get-Date).AddSeconds(15)
while ((Get-Date) -lt $startupDeadline) {
  $process.Refresh()
  if ($process.HasExited) {
    break
  }
  if (Test-ApiHealthy -Port $port) {
    $healthy = $true
    break
  }
  Start-Sleep -Milliseconds 250
}
$process.Refresh()
if ($process.HasExited -or -not $healthy) {
  if (-not $process.HasExited) {
    Stop-Process -Id $process.Id -ErrorAction SilentlyContinue
  }
  $metadata["status"] = "failed"
  $metadata["verified_at"] = (Get-Date).ToString("s")
  $metadata["failure_reason"] = "process exited or the VDO health endpoint was not ready within 15 seconds"
  $metadata | ConvertTo-Json | Set-Content -LiteralPath $metaPath -Encoding UTF8
  throw "$ServiceName failed startup verification. Check $stderrLog"
}
$metadata["status"] = "running"
$metadata["verified_at"] = (Get-Date).ToString("s")
$metadata | ConvertTo-Json | Set-Content -LiteralPath $metaPath -Encoding UTF8
if ($UpdatePortRecord) {
  Add-PortRecord -Port $port -ProcessId $process.Id -Url $url -Status "started by one-click launcher" -RecordPath $PortRecordPath
}

Write-Host "$ServiceName started at $url"
Write-Host "PID: $($process.Id)"
Write-Host "Metadata: $metaPath"
Write-Host "Stop: powershell.exe -NoProfile -File scripts\stop-ui.ps1 -ConfirmStop"

if (-not $NoBrowser) {
  Start-Process $url
}
