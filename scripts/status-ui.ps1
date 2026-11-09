param()

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
. (Join-Path $ProjectRoot "scripts\runtime-python.ps1")
Set-Location $ProjectRoot
$env:PYTHONPATH = Join-Path $ProjectRoot "src"
& $VdoPython -m video_orchestrator.cli entrypoint-status --format json
exit $LASTEXITCODE
