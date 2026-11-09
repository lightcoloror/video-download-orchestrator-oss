param(
  [switch]$ConfirmStop
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$ConfigPath = Join-Path $ProjectRoot "config\video_orchestrator.json"

if (-not $ConfirmStop) {
  throw "Stopping the VDO service requires -ConfirmStop. Run scripts\status-ui.ps1 first."
}
if (-not (Test-Path -LiteralPath $ConfigPath)) {
  throw "Missing runtime config; refusing to stop an unowned process."
}

$Config = Get-Content -LiteralPath $ConfigPath -Raw | ConvertFrom-Json
$StateDirValue = [string]$Config.runtime.state_dir
$MetaDir = if ([System.IO.Path]::IsPathRooted($StateDirValue)) {
  [System.IO.Path]::GetFullPath($StateDirValue)
} else {
  [System.IO.Path]::GetFullPath((Join-Path $ProjectRoot $StateDirValue))
}
$MetaPath = Join-Path $MetaDir "ui-server.json"
if (-not (Test-Path -LiteralPath $MetaPath)) {
  throw "Missing managed service receipt; refusing to stop an unowned process."
}
$Metadata = Get-Content -LiteralPath $MetaPath -Raw | ConvertFrom-Json
$ExpectedRoot = [System.IO.Path]::GetFullPath($ProjectRoot).TrimEnd('\')
$ReceiptRoot = [System.IO.Path]::GetFullPath([string]$Metadata.project_root).TrimEnd('\')
$PidValue = [int]$Metadata.pid
$ReceiptId = [string]$Metadata.receipt_id
$RecordedProcessStart = [string]$Metadata.process_start_time_utc
$RecordedProcessPath = [string]$Metadata.process_path

if (
  [string]$Metadata.contract -ne "vdo_ui_service_lifecycle_v1" -or
  [string]::IsNullOrWhiteSpace($ReceiptId) -or
  [string]::IsNullOrWhiteSpace($RecordedProcessStart) -or
  [string]::IsNullOrWhiteSpace($RecordedProcessPath) -or
  $ReceiptRoot -ne $ExpectedRoot -or
  [int]$Metadata.port -ne [int]$Config.web.port -or
  [string]$Metadata.host -ne [string]$Config.web.host
) {
  throw "Service receipt does not match this checkout and configured endpoint; refusing to stop."
}

function Set-ReceiptField {
  param([string]$Name, $Value)
  $Metadata | Add-Member -NotePropertyName $Name -NotePropertyValue $Value -Force
}

function Test-PortListening {
  $client = [System.Net.Sockets.TcpClient]::new()
  try {
    $connectTask = $client.ConnectAsync([string]$Config.web.host, [int]$Config.web.port)
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

$Process = Get-Process -Id $PidValue -ErrorAction SilentlyContinue
if ($null -eq $Process) {
  Set-ReceiptField -Name "status" -Value "stopped"
  Set-ReceiptField -Name "stopped_at" -Value (Get-Date).ToString("s")
  Set-ReceiptField -Name "stop_reason" -Value "recorded process was already absent"
  $Metadata | ConvertTo-Json | Set-Content -LiteralPath $MetaPath -Encoding UTF8
  Write-Host "VDO service process was already stopped; receipt updated."
  exit 0
}

$ExpectedProcessStart = [DateTimeOffset]::Parse($RecordedProcessStart).UtcDateTime
$ActualProcessStart = $Process.StartTime.ToUniversalTime()
if ([Math]::Abs(($ActualProcessStart - $ExpectedProcessStart).TotalSeconds) -gt 1) {
  throw "Recorded PID start time does not match the managed receipt; refusing to stop."
}
$ExpectedProcessPath = [System.IO.Path]::GetFullPath($RecordedProcessPath)
$ActualProcessPath = [System.IO.Path]::GetFullPath([string]$Process.Path)
if (-not $ActualProcessPath.Equals($ExpectedProcessPath, [System.StringComparison]::OrdinalIgnoreCase)) {
  throw "Recorded PID executable path does not match the managed receipt; refusing to stop."
}

$CimProcess = $null
try {
  $CimProcess = Get-CimInstance Win32_Process -Filter "ProcessId = $PidValue" -ErrorAction Stop
} catch {
  Write-Warning "Command-line inspection is unavailable; using PID, start time, executable path, and receipt identity."
}
if ($CimProcess) {
  $CommandLine = [string]$CimProcess.CommandLine
  if (
    -not $CommandLine.Contains("video_orchestrator.web_server") -or
    -not $CommandLine.Contains($ExpectedRoot) -or
    -not $CommandLine.Contains($ReceiptId)
  ) {
    throw "Recorded PID does not belong to video_orchestrator.web_server; refusing to stop."
  }
}

Stop-Process -Id $PidValue -ErrorAction Stop
$Deadline = (Get-Date).AddSeconds(10)
while ((Get-Process -Id $PidValue -ErrorAction SilentlyContinue) -and (Get-Date) -lt $Deadline) {
  Start-Sleep -Milliseconds 200
}
if (Get-Process -Id $PidValue -ErrorAction SilentlyContinue) {
  throw "Managed VDO process did not stop within 10 seconds."
}
Set-ReceiptField -Name "status" -Value "stopped"
Set-ReceiptField -Name "stopped_at" -Value (Get-Date).ToString("s")
Set-ReceiptField -Name "stop_reason" -Value "explicit ConfirmStop"
$Metadata | ConvertTo-Json | Set-Content -LiteralPath $MetaPath -Encoding UTF8

if (Test-PortListening) {
  throw "Managed PID stopped, but the configured port is still listening. Run scripts\status-ui.ps1; another process may own it."
}
Write-Host "VDO service stopped. Receipt: $MetaPath"
