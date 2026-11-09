param(
  [string]$TaskName = "VideoDownloadOrchestratorWebUI"
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$ConfigPath = Join-Path $ProjectRoot "config\video_orchestrator.json"
$PwshPath = "powershell.exe"

if (-not (Test-Path -LiteralPath $ConfigPath)) {
  throw "Missing runtime config: $ConfigPath"
}
$Config = Get-Content -LiteralPath $ConfigPath -Raw | ConvertFrom-Json
$HostAddress = [string]$Config.web.host
$Port = [int]$Config.web.port
$Scheme = [string]$Config.web.scheme
$Url = "${Scheme}://${HostAddress}:$Port/"
$ServeScript = Join-Path $ProjectRoot "scripts\serve-ui.ps1"
$StateDirValue = [string]$Config.runtime.state_dir
$MetaDir = if ([System.IO.Path]::IsPathRooted($StateDirValue)) {
  [System.IO.Path]::GetFullPath($StateDirValue)
} else {
  [System.IO.Path]::GetFullPath((Join-Path $ProjectRoot $StateDirValue))
}
$MetaPath = Join-Path $MetaDir "ui-server.json"
$ReceiptId = [guid]::NewGuid().ToString()

function Test-ApiHealthy {
  try {
    $health = Invoke-RestMethod -Uri "${Scheme}://${HostAddress}:$Port/health" -Method Get -TimeoutSec 2
    return $health.status -eq "ok" -and $health.service -eq "video-download-orchestrator"
  } catch {
    return $false
  }
}

New-Item -ItemType Directory -Force -Path $MetaDir | Out-Null

$taskAction = "$PwshPath -NoProfile -File `"$ServeScript`" -ReceiptId `"$ReceiptId`""
$startTime = (Get-Date).AddMinutes(1).ToString("HH:mm")

schtasks /Create /TN $TaskName /TR $taskAction /SC ONCE /ST $startTime /F | Out-Host
if ($LASTEXITCODE -ne 0) {
  throw "Scheduled task creation failed (schtasks exited $LASTEXITCODE)."
}
try {
  $settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Seconds 0)
  Set-ScheduledTask -TaskName $TaskName -Settings $settings | Out-Null
} catch {
  Write-Warning "Unable to remove scheduled task execution time limit: $($_.Exception.Message)"
}
schtasks /Run /TN $TaskName | Out-Host
if ($LASTEXITCODE -ne 0) {
  throw "Scheduled task start failed (schtasks exited $LASTEXITCODE)."
}

$process = $null
$healthy = $false
$startupDeadline = (Get-Date).AddSeconds(15)
while ((Get-Date) -lt $startupDeadline) {
  try {
    $process = Get-CimInstance Win32_Process |
      Where-Object {
        $_.CommandLine -and
        $_.CommandLine.Contains("video_orchestrator.web_server") -and
        $_.CommandLine.Contains($ProjectRoot) -and
        $_.CommandLine.Contains($ReceiptId)
      } |
      Sort-Object CreationDate -Descending |
      Select-Object -First 1
  } catch {
    Write-Warning "Unable to inspect Web UI process command line: $($_.Exception.Message)"
  }
  if ($process -and (Test-ApiHealthy)) {
    $healthy = $true
    break
  }
  Start-Sleep -Milliseconds 250
}

$metadata = [ordered]@{
  contract = "vdo_ui_service_lifecycle_v1"
  receipt_id = $ReceiptId
  service = "video-download-orchestrator Web UI"
  status = "starting"
  url = $Url
  host = $HostAddress
  port = $Port
  config_path = $ConfigPath
  pid = if ($process) { [int]$process.ProcessId } else { $null }
  process_start_time_utc = $null
  process_path = $null
  started_at = (Get-Date).ToString("s")
  project_root = $ProjectRoot
  launcher = "scheduled_task"
  task_name = $TaskName
  status_command = "powershell.exe -NoProfile -File scripts\status-ui.ps1"
  stop_command = "powershell.exe -NoProfile -File scripts\stop-ui.ps1 -ConfirmStop"
}
if ($null -eq $process -or -not $healthy) {
  if ($process) {
    Stop-Process -Id ([int]$process.ProcessId) -ErrorAction SilentlyContinue
  }
  $metadata["status"] = "failed"
  $metadata["verified_at"] = (Get-Date).ToString("s")
  $metadata["failure_reason"] = "scheduled process or VDO health endpoint was not verified within 15 seconds"
  $metadata | ConvertTo-Json | Set-Content -LiteralPath $MetaPath -Encoding UTF8
  throw "Scheduled Web UI failed startup verification."
}
$managedProcess = Get-Process -Id ([int]$process.ProcessId) -ErrorAction Stop
$metadata["process_start_time_utc"] = $managedProcess.StartTime.ToUniversalTime().ToString("o")
$metadata["process_path"] = $managedProcess.Path
$metadata["status"] = "running"
$metadata["verified_at"] = (Get-Date).ToString("s")
$metadata | ConvertTo-Json | Set-Content -LiteralPath $MetaPath -Encoding UTF8

Write-Host "Scheduled Web UI start requested at $Url"
Write-Host "Task: $TaskName"
Write-Host "Metadata: $MetaPath"
Write-Host "Stop: powershell.exe -NoProfile -File scripts\stop-ui.ps1 -ConfirmStop"
