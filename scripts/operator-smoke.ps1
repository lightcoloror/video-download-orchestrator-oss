param(
  [int]$StartupWaitSeconds = 3
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$ConfigPath = Join-Path $ProjectRoot "config\video_orchestrator.json"
if (-not (Test-Path -LiteralPath $ConfigPath)) {
  throw "Missing runtime config: $ConfigPath"
}

$Config = Get-Content -LiteralPath $ConfigPath -Raw | ConvertFrom-Json
$ApiBaseUrl = "$($Config.web.scheme)://$($Config.web.host):$($Config.web.port)"
$StateDirValue = [string]$Config.runtime.state_dir
$RuntimeStateDir = if ([System.IO.Path]::IsPathRooted($StateDirValue)) {
  [System.IO.Path]::GetFullPath($StateDirValue)
} else {
  [System.IO.Path]::GetFullPath((Join-Path $ProjectRoot $StateDirValue))
}
$SmokeBase = if ($env:TEST_TMP_ROOT) { $env:TEST_TMP_ROOT } else { $RuntimeStateDir }
$SmokeRoot = Join-Path $SmokeBase "operator-smoke"
$DbPath = Join-Path $SmokeRoot "tasks.sqlite"
New-Item -ItemType Directory -Force -Path $SmokeRoot | Out-Null
$StartedService = $false

function Test-Api {
  try {
    $health = Invoke-RestMethod -Uri "$ApiBaseUrl/health" -Method Get -TimeoutSec 3
    return $health.status -eq "ok"
  } catch {
    return $false
  }
}

try {
  if (-not (Test-Api)) {
    & (Join-Path $ProjectRoot "scripts\start-ui.ps1") -NoBrowser | Out-Host
    $StartedService = $true
    Start-Sleep -Seconds $StartupWaitSeconds
  }

  if (-not (Test-Api)) {
    throw "API did not become healthy at configured URL: $ApiBaseUrl"
  }

  & (Join-Path $ProjectRoot "scripts\smoke-page-link.ps1") -StartupWaitSeconds $StartupWaitSeconds | Out-Host

$env:PYTHONPATH = Join-Path $ProjectRoot "src"
$env:PYTHONUTF8 = "1"
$Stamp = Get-Date -Format "yyyyMMddHHmmss"
$SessionId = "operator-smoke-$Stamp"
$PageUrl = "https://example.com/operator-smoke/${Stamp}?reload=1"
$SpecPath = Join-Path $SmokeRoot "retryable-batch-$Stamp.json"
$SpecJson = @{ items = @(@{ url = $PageUrl }) } | ConvertTo-Json -Depth 5
[System.IO.File]::WriteAllText($SpecPath, $SpecJson, (New-Object System.Text.UTF8Encoding($false)))

$RetryOutput = python -m video_orchestrator.cli batch $SpecPath --execute --page-link-mode sniff --sniff-timeout 1 --sniff-poll-interval 0.1 --output-dir $SmokeRoot --db-path $DbPath --format json
$RetryPayload = ($RetryOutput -join [Environment]::NewLine) | ConvertFrom-Json
if ($RetryPayload.status -eq "failed") {
  throw "Batch command failed: $($RetryOutput -join [Environment]::NewLine)"
}
$RetryItems = @($RetryPayload | Select-Object -ExpandProperty items -ErrorAction Stop)
if ($RetryItems.Count -eq 0) {
  throw "Batch output did not include items: $($RetryOutput -join [Environment]::NewLine)"
}
$RetryItem = $RetryItems[0]
if ($RetryItem.status -ne "retryable") {
  throw "Expected retryable batch item, got: $($RetryItem | ConvertTo-Json -Depth 8)"
}
$TaskId = [int]$RetryItem.task.id

$Payload = @{
  url = "https://cdn.example.com/operator-smoke/$Stamp/master.m3u8"
  page_url = $PageUrl
  capture_session_id = $SessionId
  category = "stream"
  media_type = "HLS"
  content_type = "application/vnd.apple.mpegurl"
  response_headers = @{ "content-type" = "application/vnd.apple.mpegurl" }
  output_dir = $SmokeRoot
  write_manifest = $true
} | ConvertTo-Json -Depth 8
Invoke-RestMethod -Uri "$ApiBaseUrl/sniffed-media/plan" -Method Post -ContentType "application/json" -Body $Payload | Out-Null

$ApiOperatorStatus = Invoke-RestMethod -Uri "$ApiBaseUrl/operator/status?db_path=$([System.Web.HttpUtility]::UrlEncode($DbPath))" -Method Get
if ($ApiOperatorStatus.status -ne "ok") {
  throw "Expected /operator/status ok, got: $($ApiOperatorStatus.status)"
}
$ApiRetryable = Invoke-RestMethod -Uri "$ApiBaseUrl/operator/tasks/retryable?db_path=$([System.Web.HttpUtility]::UrlEncode($DbPath))" -Method Get
if ($ApiRetryable.status -ne "ok") {
  throw "Expected /operator/tasks/retryable ok, got: $($ApiRetryable.status)"
}
$Diagnostics = Invoke-RestMethod -Uri "$ApiBaseUrl/sniffed-media/diagnostics?limit=5" -Method Get
if ($Diagnostics.status -ne "ok") {
  throw "Expected /sniffed-media/diagnostics ok, got: $($Diagnostics.status)"
}
$Handoff = Invoke-RestMethod -Uri "$ApiBaseUrl/operator/handoff?url=$([System.Web.HttpUtility]::UrlEncode($PageUrl))&capture_session=$([System.Web.HttpUtility]::UrlEncode($SessionId))" -Method Get
if ($Handoff.will_download -ne $false) {
  throw "Expected /operator/handoff will_download=false"
}

$ResumeOutput = python -m video_orchestrator.cli task resume $TaskId --db-path $DbPath --output-dir $SmokeRoot --capture-session $SessionId --sniff-timeout 3 --sniff-poll-interval 0.1 --dry-run --format json
$ResumePayload = ($ResumeOutput -join [Environment]::NewLine) | ConvertFrom-Json
if ($ResumePayload.status -ne "planned") {
  throw "Expected task resume dry-run status planned, got: $($ResumePayload.status)"
}
$TaskHandoffOutput = python -m video_orchestrator.cli task handoff $TaskId --db-path $DbPath --format json
$TaskHandoffPayload = ($TaskHandoffOutput -join [Environment]::NewLine) | ConvertFrom-Json
if ($TaskHandoffPayload.will_download -ne $false) {
  throw "Expected task handoff will_download=false"
}

$ReviewRoot = Join-Path $SmokeRoot "review-source-$Stamp"
$ReviewDir = Join-Path $ReviewRoot ".vdo\reports\finished-smoke"
New-Item -ItemType Directory -Force -Path $ReviewDir | Out-Null
$FinishedTaskOutput = python -m video_orchestrator.cli task create "https://example.com/operator-smoke/finished-$Stamp.mp4" --db-path $DbPath --format json
$FinishedTaskPayload = ($FinishedTaskOutput -join [Environment]::NewLine) | ConvertFrom-Json
$FinishedTaskId = [int]$FinishedTaskPayload.task.id
$ReportPath = Join-Path $ReviewDir "report.md"
$ReviewChecklistPath = Join-Path $ReviewDir "review-checklist.json"
[System.IO.File]::WriteAllText($ReportPath, "# smoke report`n", (New-Object System.Text.UTF8Encoding($false)))
$ReviewChecklistJson = @{
  checks = @(@{ name = "output_files"; status = "ok" })
  backend_result = @{ size_matches_page = $true }
} | ConvertTo-Json -Depth 8
[System.IO.File]::WriteAllText($ReviewChecklistPath, $ReviewChecklistJson, (New-Object System.Text.UTF8Encoding($false)))
$ReviewDbScript = @"
from pathlib import Path
from video_orchestrator.runtime.task_store import TaskStore
db = Path(r'$DbPath')
task_id = $FinishedTaskId
store = TaskStore(db)
store.mark_finished(task_id, 0)
store.add_artifact(task_id, 'report', Path(r'$ReviewDir') / 'report.md')
store.add_artifact(task_id, 'review_checklist', Path(r'$ReviewDir') / 'review-checklist.json')
"@
$ReviewDbScriptPath = Join-Path $SmokeRoot "mark-finished-$Stamp.py"
[System.IO.File]::WriteAllText($ReviewDbScriptPath, $ReviewDbScript, (New-Object System.Text.UTF8Encoding($false)))
python $ReviewDbScriptPath
$ReviewPackageDir = Join-Path $SmokeRoot "review-packages"
$ExportOutput = python -m video_orchestrator.cli task export-review $FinishedTaskId --db-path $DbPath --output-dir $ReviewPackageDir --format json
$ExportPayload = ($ExportOutput -join [Environment]::NewLine) | ConvertFrom-Json
if ($ExportPayload.status -ne "ok") {
  throw "Expected task export-review status ok, got: $($ExportPayload.status)"
}

$SniffDiagnosticsDir = Join-Path $SmokeRoot ".vdo"
$SniffDiagnosticsOutput = python -m video_orchestrator.cli sniff-diagnostics --limit 5 --base-dir $SniffDiagnosticsDir --format json
$SniffDiagnosticsPayload = ($SniffDiagnosticsOutput -join [Environment]::NewLine) | ConvertFrom-Json
if ($SniffDiagnosticsPayload.status -ne "ok") {
  throw "Expected sniff-diagnostics status ok, got: $($SniffDiagnosticsPayload.status)"
}

$RetryableOutput = python -m video_orchestrator.cli task retryable --db-path $DbPath --format json
$RetryablePayload = ($RetryableOutput -join [Environment]::NewLine) | ConvertFrom-Json
$DoctorOutput = python -m video_orchestrator.cli operator-doctor --db-path $DbPath --format json
$DoctorPayload = ($DoctorOutput -join [Environment]::NewLine) | ConvertFrom-Json

  [pscustomobject]@{
    service = "video-download-orchestrator"
    status = "ok"
    api_base_url = $ApiBaseUrl
    page_smoke = "ok"
    retryable_task_id = $TaskId
    retryable_count = $RetryablePayload.tasks.Count
    operator_api_retryable_count = $ApiOperatorStatus.retryable_task_count
    handoff_status = $Handoff.status
    task_handoff_status = $TaskHandoffPayload.status
    resume_status = $ResumePayload.status
    export_review_status = $ExportPayload.status
    sniff_diagnostics_status = $SniffDiagnosticsPayload.status
    operator_doctor_status = $DoctorPayload.status
  } | ConvertTo-Json -Depth 5
} finally {
  if ($StartedService) {
    & (Join-Path $ProjectRoot "scripts\stop-ui.ps1") -ConfirmStop | Out-Host
  }
}
