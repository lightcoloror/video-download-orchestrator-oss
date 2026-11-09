$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot "runtime-python.ps1")
$env:PYTHONPATH = Join-Path $ProjectRoot "src"
& $VdoPython -m video_orchestrator.cli @args
exit $LASTEXITCODE
