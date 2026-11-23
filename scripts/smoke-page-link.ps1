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
$SmokeDir = Join-Path $SmokeBase "smoke-page-link"
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

  $CapturedPage = "https://example.com/smoke-page-link?reload=1#from-player"
  $QueryPage = "https://example.com/smoke-page-link?from=3"
  $SessionId = "smoke-page-link"
  $Payload = @{
    url = "https://cdn.example.com/live/smoke-page-link.m3u8"
    page_url = $CapturedPage
    capture_session_id = $SessionId
    category = "stream"
    media_type = "HLS"
    content_type = "application/vnd.apple.mpegurl"
    response_headers = @{ "content-type" = "application/vnd.apple.mpegurl" }
    output_dir = $SmokeDir
    write_manifest = $true
  } | ConvertTo-Json -Depth 8

  $Plan = Invoke-RestMethod -Uri "$ApiBaseUrl/sniffed-media/plan" -Method Post -ContentType "application/json" -Body $Payload
  $EncodedPage = [System.Web.HttpUtility]::UrlEncode($QueryPage)
  $EncodedSession = [System.Web.HttpUtility]::UrlEncode($SessionId)
  $Latest = Invoke-RestMethod -Uri "$ApiBaseUrl/sniffed-media/latest?page_url=$EncodedPage&capture_session_id=$EncodedSession" -Method Get

  if ($Latest.status -ne "ok") {
    throw "Expected latest capture status ok, got: $($Latest.status)"
  }
  if ($Latest.capture_page_match -ne "normalized") {
    throw "Expected capture_page_match=normalized, got: $($Latest.capture_page_match)"
  }

  $env:PYTHONPATH = Join-Path $ProjectRoot "src"
  $env:PYTHONUTF8 = "1"
  $CliOutput = python -m video_orchestrator.cli download-page $QueryPage --capture-session $SessionId --dry-run --output-dir $SmokeDir --format json
  $CliPayload = $CliOutput | ConvertFrom-Json
  if ($CliPayload.status -ne "planned") {
    throw "Expected download-page dry-run status planned, got: $($CliPayload.status)"
  }

  [pscustomobject]@{
    service = "video-download-orchestrator"
    status = "ok"
    api_base_url = $ApiBaseUrl
    plan_status = $Plan.status
    latest_status = $Latest.status
    capture_page_match = $Latest.capture_page_match
    dry_run_status = $CliPayload.status
    manifest_path = $CliPayload.manifest_path
  } | ConvertTo-Json -Depth 5
} finally {
  if ($StartedService) {
    & (Join-Path $ProjectRoot "scripts\stop-ui.ps1") -ConfirmStop | Out-Host
  }
}
