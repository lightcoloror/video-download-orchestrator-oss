# Dot-source from project launchers to use the same interpreter and backend PATH.
$VdoProjectRoot = Split-Path -Parent $PSScriptRoot
$VdoScriptsDir = Join-Path $VdoProjectRoot ".venv\Scripts"
$VdoLocalPython = Join-Path $VdoScriptsDir "python.exe"
if (Test-Path -LiteralPath $VdoLocalPython) {
  $VdoPython = $VdoLocalPython
  $env:VIRTUAL_ENV = Join-Path $VdoProjectRoot ".venv"
  if (($env:PATH -split ';') -notcontains $VdoScriptsDir) {
    $env:PATH = "$VdoScriptsDir;$env:PATH"
  }
} else {
  $VdoPython = (Get-Command python -ErrorAction Stop).Source
}
