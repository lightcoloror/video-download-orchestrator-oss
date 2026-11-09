param(
    [string]$Python = "python"
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $ProjectRoot

$env:PYTHONPATH = Join-Path $ProjectRoot "src"
& $Python -m video_orchestrator.mcp_server
exit $LASTEXITCODE
