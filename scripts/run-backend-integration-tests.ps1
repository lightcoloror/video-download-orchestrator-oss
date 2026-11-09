param(
    [string]$Python = "python",
    [string[]]$PytestArgs = @("-q")
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $ProjectRoot

$env:PYTHONPATH = Join-Path $ProjectRoot "src"
$env:VDO_RUN_BACKEND_INTEGRATION = "1"

& $Python -m pytest tests\test_backend_binary_integration.py @PytestArgs
exit $LASTEXITCODE
