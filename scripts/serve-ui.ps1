param(
  [string]$ReceiptId = "foreground"
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
. (Join-Path $ProjectRoot "scripts\runtime-python.ps1")
$ConfigPath = Join-Path $ProjectRoot "config\video_orchestrator.json"
$LocalAllowlistPath = Join-Path $ProjectRoot ".vdo\openclaw-telegram-allowlist.txt"

if (-not (Test-Path -LiteralPath $ConfigPath)) {
  throw "Missing runtime config: $ConfigPath"
}

Set-Location $ProjectRoot

$env:PYTHONPATH = Join-Path $ProjectRoot "src"
if (-not $env:VDO_TELEGRAM_ALLOWED_USER_IDS -and (Test-Path -LiteralPath $LocalAllowlistPath)) {
  $allowedActorIds = Get-Content -LiteralPath $LocalAllowlistPath |
    ForEach-Object { $_.Trim() } |
    Where-Object { $_ -and -not $_.StartsWith("#") }
  if ($allowedActorIds) {
    $env:VDO_TELEGRAM_ALLOWED_USER_IDS = ($allowedActorIds -join ",")
  }
}

& $VdoPython scripts\sync_runtime_config.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $VdoPython -m video_orchestrator.config check | Out-Host
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $VdoPython -m video_orchestrator.web_server --vdo-project-root $ProjectRoot --vdo-receipt-id $ReceiptId
exit $LASTEXITCODE
