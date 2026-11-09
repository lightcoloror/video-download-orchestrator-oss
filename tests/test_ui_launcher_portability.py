"""Offline synthetic control tests for the VDO Windows UI lifecycle launchers.

These tests are deliberately non-integration: they never start the Web UI, a
scheduled task, a process lookup/stop/start, an HTTP/TCP endpoint, or a real
Python module.  They parse the candidate PowerShell sources with Windows
PowerShell 5.1, then exercise copied fixtures whose reached external commands
are replaced by test-only stubs and whose temporary files stay in the test
temporary directory.

What these tests cover is wrapper control flow: explicit PathRecord gating,
Python-wrapper exit-code propagation, and scheduler exit-code short-circuits.
They do not prove real interpreter selection, real process ownership, real
scheduler behavior, real schema/business validity, service health, or any
cross-platform behavior.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"

POWERSHELL = shutil.which("powershell.exe") or shutil.which("powershell")
WINDOWS_POWERSHELL = sys.platform == "win32" and POWERSHELL is not None

PS1_CANDIDATES = {
    "start-ui.ps1": SCRIPTS / "start-ui.ps1",
    "status-ui.ps1": SCRIPTS / "status-ui.ps1",
    "stop-ui.ps1": SCRIPTS / "stop-ui.ps1",
    "serve-ui.ps1": SCRIPTS / "serve-ui.ps1",
    "start-ui-scheduled.ps1": SCRIPTS / "start-ui-scheduled.ps1",
}

# Synthetic config placeholders are wrapper fixtures only.  They are NOT
# schema-valid, business-valid, or real VDO configuration documents.
SERVE_PRESENCE_CONFIG = '{"_synthetic": "presence placeholder only; not a VDO config"}\n'
SCHEDULED_WRAPPER_CONFIG = (
    '{"_synthetic": "wrapper fixture only; not a schema-valid VDO config",'
    ' "web": {"host": "127.0.0.1", "port": 1, "scheme": "http"},'
    ' "runtime": {"state_dir": "state"}}\n'
)

PARSE_DRIVER = r"""
param(
  [Parameter(Mandatory = $true)][string]$InputJson,
  [Parameter(Mandatory = $true)][string]$OutputJson
)

$files = Get-Content -LiteralPath $InputJson -Raw | ConvertFrom-Json
$results = @()
foreach ($file in $files) {
  $tokens = $null
  $errors = $null
  [System.Management.Automation.Language.Parser]::ParseFile([string]$file, [ref]$tokens, [ref]$errors) | Out-Null
  $messages = @()
  foreach ($parseError in @($errors)) {
    if ($parseError) {
      $messages += ("line " + $parseError.Extent.StartLineNumber + ", column " + $parseError.Extent.StartColumnNumber + ": " + $parseError.Message)
    }
  }
  $results += [ordered]@{
    path = [string]$file
    error_count = @($errors).Count
    messages = $messages
  }
}
[ordered]@{
  ps_version = $PSVersionTable.PSVersion.ToString()
  edition = $PSEdition
  files = $results
} | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $OutputJson -Encoding UTF8
"""

PORT_RECORD_DRIVER = r"""
param(
  [Parameter(Mandatory = $true)][string]$SourceScript,
  [Parameter(Mandatory = $true)][string]$WorkDir,
  [Parameter(Mandatory = $true)][string]$ResultPath
)

$tokens = $null
$parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($SourceScript, [ref]$tokens, [ref]$parseErrors)
if (@($parseErrors).Count -gt 0) {
  throw "Candidate start-ui.ps1 has parse errors; cannot extract Add-PortRecord."
}
$functionAst = $ast.Find(
  { param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Add-PortRecord' },
  $true
)
if ($null -eq $functionAst) {
  throw "Add-PortRecord function was not found in the candidate launcher."
}

function global:Get-Date {
  param([string]$Format)
  return "2026-01-02 03:04:05"
}
$global:SyntheticAppendCalls = 0
$global:SyntheticAppendMode = "count"
function global:Add-Content {
  param([string]$LiteralPath, $Value, [string]$Encoding)
  $global:SyntheticAppendCalls = $global:SyntheticAppendCalls + 1
  if ($global:SyntheticAppendMode -eq "real") {
    Microsoft.PowerShell.Management\Add-Content -LiteralPath $LiteralPath -Value $Value -Encoding $Encoding
  }
}

Invoke-Expression $functionAst.Extent.Text
$ServiceName = "Synthetic VDO Service"
$ProjectRoot = "C:\synthetic\project-root"
$HostAddress = "127.0.0.1"

$emptyPath = ""
$missingPath = Join-Path $WorkDir "missing-port-record.md"
$suppliedPath = Join-Path $WorkDir "supplied-port-record.md"
Set-Content -LiteralPath $suppliedPath -Value "# synthetic port record header" -Encoding UTF8

$startCount = $global:SyntheticAppendCalls
Add-PortRecord -Port 8443 -ProcessId 4242 -Url "http://127.0.0.1:8443/" -Status "synthetic" -RecordPath $emptyPath
$emptyAdded = $global:SyntheticAppendCalls - $startCount

$startCount = $global:SyntheticAppendCalls
Add-PortRecord -Port 8443 -ProcessId 4242 -Url "http://127.0.0.1:8443/" -Status "synthetic" -RecordPath $missingPath
$missingAdded = $global:SyntheticAppendCalls - $startCount

$global:SyntheticAppendMode = "real"
$beforeLength = (Get-Item -LiteralPath $suppliedPath).Length
$startCount = $global:SyntheticAppendCalls
Add-PortRecord -Port 8443 -ProcessId 4242 -Url "http://127.0.0.1:8443/" -Status "synthetic" -RecordPath $suppliedPath
$suppliedAdded = $global:SyntheticAppendCalls - $startCount
$afterLength = (Get-Item -LiteralPath $suppliedPath).Length
$suppliedContent = Get-Content -LiteralPath $suppliedPath -Raw

[ordered]@{
  function_found = $true
  empty_added = $emptyAdded
  missing_added = $missingAdded
  missing_exists = (Test-Path -LiteralPath $missingPath)
  supplied_added = $suppliedAdded
  supplied_grew = ($afterLength -gt $beforeLength)
  supplied_content = $suppliedContent
} | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $ResultPath -Encoding UTF8
"""

SYNTHETIC_RUNTIME_HELPER = r"""
# Synthetic, test-only stand-in for scripts/runtime-python.ps1.
# It selects no interpreter and launches no Python module, CLI, or network
# call.  It only records the argument list passed through $VdoPython and sets
# the global $LASTEXITCODE that the wrapper under test is expected to check.
$VdoPython = "SyntheticVdoPython"
$global:SyntheticVdoCallIndex = 0
$global:SyntheticVdoExitSequence = @()
if ($env:SYNTH_EXIT_SEQUENCE) {
  $global:SyntheticVdoExitSequence = @($env:SYNTH_EXIT_SEQUENCE -split ',' | ForEach-Object { [int]$_ })
}

function global:SyntheticVdoPython {
  param([Parameter(ValueFromRemainingArguments = $true)][object[]]$CommandArgs)
  $line = ($CommandArgs | ForEach-Object { [string]$_ }) -join ' '
  if ($env:SYNTH_CALL_LOG) {
    Add-Content -LiteralPath $env:SYNTH_CALL_LOG -Value $line
  }
  $code = 0
  if ($global:SyntheticVdoCallIndex -lt $global:SyntheticVdoExitSequence.Count) {
    $code = [int]$global:SyntheticVdoExitSequence[$global:SyntheticVdoCallIndex]
  }
  $global:SyntheticVdoCallIndex = $global:SyntheticVdoCallIndex + 1
  $global:LASTEXITCODE = $code
}
"""

SCHEDULED_DRIVER = r"""
param(
  [Parameter(Mandatory = $true)][string]$FixtureRoot,
  [Parameter(Mandatory = $true)][string]$ExitSequence,
  [Parameter(Mandatory = $true)][string]$CallLog,
  [Parameter(Mandatory = $true)][string]$ResultPath
)

$global:SyntheticCallLog = $CallLog
$global:SyntheticExitSequence = @($ExitSequence -split ',' | ForEach-Object { [int]$_ })
$global:SyntheticIndex = 0
if (Test-Path -LiteralPath $global:SyntheticCallLog) {
  Remove-Item -LiteralPath $global:SyntheticCallLog -Force
}

function global:Write-SyntheticCall {
  param([string]$Name, [string]$Detail)
  Add-Content -LiteralPath $global:SyntheticCallLog -Value ($Name + ' ' + $Detail)
}

function global:Next-SyntheticExitCode {
  $code = 0
  if ($global:SyntheticIndex -lt $global:SyntheticExitSequence.Count) {
    $code = [int]$global:SyntheticExitSequence[$global:SyntheticIndex]
  }
  $global:SyntheticIndex = $global:SyntheticIndex + 1
  return $code
}

function global:schtasks {
  param([Parameter(ValueFromRemainingArguments = $true)][object[]]$CommandArgs)
  $detail = ($CommandArgs | ForEach-Object { [string]$_ }) -join ' '
  Write-SyntheticCall -Name 'schtasks' -Detail $detail
  $global:LASTEXITCODE = Next-SyntheticExitCode
  Write-Output 'synthetic schtasks output'
}

function global:New-ScheduledTaskSettingsSet {
  [CmdletBinding()]
  param(
    [object]$ExecutionTimeLimit,
    [Parameter(ValueFromRemainingArguments = $true)][object[]]$CommandArgs
  )
  Write-SyntheticCall -Name 'New-ScheduledTaskSettingsSet' -Detail ([string]$ExecutionTimeLimit)
  return [pscustomobject]@{ Synthetic = $true }
}

function global:Set-ScheduledTask {
  [CmdletBinding()]
  param(
    [string]$TaskName,
    [object]$Settings,
    [Parameter(ValueFromRemainingArguments = $true)][object[]]$CommandArgs
  )
  Write-SyntheticCall -Name 'Set-ScheduledTask' -Detail ([string]$TaskName)
}

function global:Get-CimInstance {
  [CmdletBinding()]
  param(
    [string]$Filter,
    [Parameter(ValueFromRemainingArguments = $true)][object[]]$CommandArgs
  )
  Write-SyntheticCall -Name 'UnexpectedCall:Get-CimInstance' -Detail ([string]$Filter)
  throw 'UNEXPECTED_CALL Get-CimInstance'
}

function global:Get-Process {
  [CmdletBinding()]
  param(
    [object]$Id,
    [Parameter(ValueFromRemainingArguments = $true)][object[]]$CommandArgs
  )
  Write-SyntheticCall -Name 'UnexpectedCall:Get-Process' -Detail ([string]$Id)
  throw 'UNEXPECTED_CALL Get-Process'
}

function global:Invoke-RestMethod {
  [CmdletBinding()]
  param(
    [string]$Uri,
    [string]$Method,
    [int]$TimeoutSec,
    [Parameter(ValueFromRemainingArguments = $true)][object[]]$CommandArgs
  )
  Write-SyntheticCall -Name 'UnexpectedCall:Invoke-RestMethod' -Detail ([string]$Uri)
  throw 'UNEXPECTED_CALL Invoke-RestMethod'
}

function global:Stop-Process {
  [CmdletBinding()]
  param(
    [object]$Id,
    [Parameter(ValueFromRemainingArguments = $true)][object[]]$CommandArgs
  )
  Write-SyntheticCall -Name 'UnexpectedCall:Stop-Process' -Detail ([string]$Id)
  throw 'UNEXPECTED_CALL Stop-Process'
}

function global:Start-Sleep {
  [CmdletBinding()]
  param(
    [object]$Milliseconds,
    [Parameter(ValueFromRemainingArguments = $true)][object[]]$CommandArgs
  )
  Write-SyntheticCall -Name 'UnexpectedCall:Start-Sleep' -Detail ([string]$Milliseconds)
  throw 'UNEXPECTED_CALL Start-Sleep'
}

$thrown = $null
$target = Join-Path $FixtureRoot 'scripts\start-ui-scheduled.ps1'
try {
  & $target
} catch {
  $thrown = $_.Exception.Message
}

$receiptPath = Join-Path $FixtureRoot 'state\ui-server.json'
[ordered]@{
  thrown = $thrown
  receipt_present = (Test-Path -LiteralPath $receiptPath)
} | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $ResultPath -Encoding UTF8
"""


def _new_workdir(prefix: str) -> Path:
    return Path(tempfile.mkdtemp(prefix=prefix))


def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _run_powershell_script(script: Path, arguments=(), env_extra=None, workdir: Path = None, timeout: int = 180):
    if not WINDOWS_POWERSHELL:
        raise unittest.SkipTest("Windows PowerShell (powershell.exe) is unavailable")
    env = os.environ.copy()
    if workdir is not None:
        child_temp = workdir / "child-temp"
        child_temp.mkdir(parents=True, exist_ok=True)
        for variable in ("TEMP", "TMP", "TMPDIR"):
            env[variable] = str(child_temp)
    if env_extra:
        env.update({key: str(value) for key, value in env_extra.items()})
    command = [POWERSHELL, "-NoProfile", "-File", str(script)]
    command.extend(str(argument) for argument in arguments)
    return subprocess.run(
        command,
        cwd=str(workdir) if workdir is not None else None,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout,
    )


@unittest.skipUnless(WINDOWS_POWERSHELL, "Windows PowerShell 5.1 is required")
class WindowsPowerShellSyntaxTests(unittest.TestCase):
    def test_five_candidate_scripts_parse_with_windows_powershell_51(self):
        for path in PS1_CANDIDATES.values():
            self.assertTrue(path.is_file(), f"missing candidate script: {path}")

        workdir = _new_workdir("vdo-ui-parse-")
        files_json = workdir / "files.json"
        files_json.write_text(json.dumps([str(p) for p in PS1_CANDIDATES.values()]), encoding="utf-8")
        driver = workdir / "parse_driver.ps1"
        driver.write_text(PARSE_DRIVER, encoding="utf-8")
        result_json = workdir / "parse-result.json"

        proc = _run_powershell_script(
            driver,
            ["-InputJson", files_json, "-OutputJson", result_json],
            workdir=workdir,
        )
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        result = _read_json(result_json)

        self.assertTrue(result["ps_version"].startswith("5.1"), result["ps_version"])
        self.assertEqual("Desktop", result["edition"])
        self.assertEqual(len(PS1_CANDIDATES), len(result["files"]))
        for item in result["files"]:
            self.assertEqual(0, item["error_count"], f"{item['path']}: {item['messages']}")


class StaticLauncherControlTests(unittest.TestCase):
    def test_update_port_record_is_the_only_main_call_gate(self):
        text = (SCRIPTS / "start-ui.ps1").read_text(encoding="utf-8")

        self.assertNotIn('$PortRecord = "', text)
        self.assertEqual(1, text.count("if ($UpdatePortRecord)"))
        gate = re.search(
            r"if \(\$UpdatePortRecord\) \{\r?\n"
            r"\s*Add-PortRecord -Port \$port -ProcessId \$process\.Id -Url \$url "
            r'-Status "started by one-click launcher" -RecordPath \$PortRecordPath\r?\n\}',
            text,
        )
        self.assertIsNotNone(gate)
        self.assertIn('[string]$PortRecordPath = ""', text)
        self.assertNotIn("ExecutionPolicy Bypass", text)


@unittest.skipUnless(WINDOWS_POWERSHELL, "Windows PowerShell 5.1 is required")
class PortRecordControlTests(unittest.TestCase):
    def test_add_port_record_requires_an_existing_explicit_path(self):
        workdir = _new_workdir("vdo-ui-port-record-")
        driver = workdir / "port-record-driver.ps1"
        driver.write_text(PORT_RECORD_DRIVER, encoding="utf-8")
        result_json = workdir / "port-record-result.json"

        proc = _run_powershell_script(
            driver,
            [
                "-SourceScript", SCRIPTS / "start-ui.ps1",
                "-WorkDir", workdir,
                "-ResultPath", result_json,
            ],
            workdir=workdir,
        )
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        result = _read_json(result_json)

        self.assertTrue(result["function_found"])
        self.assertEqual(0, result["empty_added"], "empty explicit target must not append")
        self.assertEqual(0, result["missing_added"], "missing explicit target must not append")
        self.assertFalse(result["missing_exists"], "missing explicit target must not be created")
        self.assertEqual(1, result["supplied_added"], "supplied synthetic target gets exactly one append")
        self.assertTrue(result["supplied_grew"])

        content = result["supplied_content"]
        self.assertEqual(1, content.count("### "))
        self.assertIn("### 2026-01-02 03:04:05 | PowerShell launcher (operator-triggered)", content)
        self.assertIn("- Service: Synthetic VDO Service", content)
        self.assertIn("- URL: http://127.0.0.1:8443/", content)
        self.assertIn("- Port: 8443", content)
        self.assertIn("- PID: 4242", content)
        self.assertIn("- Status: synthetic", content)
        self.assertIn("- Workspace: C:\\synthetic\\project-root", content)


@unittest.skipUnless(WINDOWS_POWERSHELL, "Windows PowerShell 5.1 is required")
class ServeUiFixtureTests(unittest.TestCase):
    def _fixture(self):
        workdir = _new_workdir("vdo-ui-serve-")
        repo = workdir / "repo"
        (repo / "scripts").mkdir(parents=True)
        (repo / "config").mkdir()
        shutil.copyfile(SCRIPTS / "serve-ui.ps1", repo / "scripts" / "serve-ui.ps1")
        (repo / "scripts" / "runtime-python.ps1").write_text(
            SYNTHETIC_RUNTIME_HELPER, encoding="utf-8"
        )
        (repo / "config" / "video_orchestrator.json").write_text(
            SERVE_PRESENCE_CONFIG, encoding="utf-8"
        )
        return workdir, repo

    def _run_case(self, exit_sequence: str):
        workdir, repo = self._fixture()
        call_log = workdir / "python-calls.log"
        proc = _run_powershell_script(
            repo / "scripts" / "serve-ui.ps1",
            ["-ReceiptId", "synthetic-receipt"],
            env_extra={
                "SYNTH_CALL_LOG": call_log,
                "SYNTH_EXIT_SEQUENCE": exit_sequence,
            },
            workdir=workdir,
        )
        calls = call_log.read_text(encoding="utf-8").splitlines() if call_log.is_file() else []
        return proc, calls

    def test_sync_failure_stops_before_config_check_and_server(self):
        proc, calls = self._run_case("3")

        self.assertEqual(3, proc.returncode, proc.stdout + proc.stderr)
        self.assertEqual(1, len(calls), calls)
        self.assertTrue(calls[0].endswith("sync_runtime_config.py"), calls)

    def test_config_failure_stops_before_server(self):
        proc, calls = self._run_case("0,2")

        self.assertEqual(2, proc.returncode, proc.stdout + proc.stderr)
        self.assertEqual(2, len(calls), calls)
        self.assertTrue(calls[1].startswith("-m video_orchestrator.config check"), calls)
        self.assertFalse(any("web_server" in call for call in calls), calls)

    def test_server_exit_is_forwarded(self):
        proc, calls = self._run_case("0,0,7")

        self.assertEqual(7, proc.returncode, proc.stdout + proc.stderr)
        self.assertEqual(3, len(calls), calls)
        self.assertIn("-m video_orchestrator.web_server", calls[2])
        self.assertIn("--vdo-receipt-id synthetic-receipt", calls[2])

    def test_success_retains_all_wrapper_calls(self):
        proc, calls = self._run_case("0,0,0")

        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        self.assertEqual(3, len(calls), calls)
        self.assertTrue(calls[0].endswith("sync_runtime_config.py"))
        self.assertTrue(calls[1].startswith("-m video_orchestrator.config check"))
        self.assertIn("-m video_orchestrator.web_server", calls[2])


@unittest.skipUnless(WINDOWS_POWERSHELL, "Windows PowerShell 5.1 is required")
class ScheduledFixtureTests(unittest.TestCase):
    def _run_case(self, exit_sequence: str):
        workdir = _new_workdir("vdo-ui-scheduled-")
        repo = workdir / "repo"
        (repo / "scripts").mkdir(parents=True)
        (repo / "config").mkdir()
        shutil.copyfile(
            SCRIPTS / "start-ui-scheduled.ps1", repo / "scripts" / "start-ui-scheduled.ps1"
        )
        (repo / "config" / "video_orchestrator.json").write_text(
            SCHEDULED_WRAPPER_CONFIG, encoding="utf-8"
        )
        driver = workdir / "scheduled-driver.ps1"
        driver.write_text(SCHEDULED_DRIVER, encoding="utf-8")
        call_log = workdir / "scheduler-calls.log"
        result_json = workdir / "scheduled-result.json"

        proc = _run_powershell_script(
            driver,
            [
                "-FixtureRoot", repo,
                "-ExitSequence", exit_sequence,
                "-CallLog", call_log,
                "-ResultPath", result_json,
            ],
            workdir=workdir,
        )
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        calls = call_log.read_text(encoding="utf-8").splitlines() if call_log.is_file() else []
        return _read_json(result_json), calls

    def test_create_failure_stops_before_settings_run_and_cim(self):
        result, calls = self._run_case("1")

        self.assertIsNotNone(result["thrown"])
        self.assertIn("creation failed", result["thrown"])
        self.assertNotIn("UNEXPECTED_CALL", result["thrown"])
        self.assertFalse(result["receipt_present"], "no receipt may be written after a create failure")
        self.assertEqual(1, len(calls), calls)
        self.assertTrue(calls[0].startswith("schtasks "))
        self.assertIn("/Create", calls[0])
        self.assertFalse(any("/Run" in call for call in calls), calls)
        self.assertFalse(any("New-ScheduledTaskSettingsSet" in call for call in calls), calls)
        self.assertFalse(any("Set-ScheduledTask" in call for call in calls), calls)
        self.assertFalse(any("Get-CimInstance" in call for call in calls), calls)

    def test_run_failure_stops_before_cim_health_and_receipts(self):
        result, calls = self._run_case("0,1")

        self.assertIsNotNone(result["thrown"])
        self.assertIn("start failed", result["thrown"])
        self.assertNotIn("UNEXPECTED_CALL", result["thrown"])
        self.assertFalse(result["receipt_present"], "no receipt may be written after a run failure")
        self.assertEqual(4, len(calls), calls)
        self.assertTrue(calls[0].startswith("schtasks "))
        self.assertIn("/Create", calls[0])
        self.assertTrue(calls[1].startswith("New-ScheduledTaskSettingsSet "), calls[1])
        self.assertTrue(calls[2].startswith("Set-ScheduledTask "), calls[2])
        self.assertTrue(calls[3].startswith("schtasks "), calls[3])
        self.assertIn("/Run", calls[3])
        self.assertFalse(any("UnexpectedCall" in call for call in calls), calls)


if __name__ == "__main__":
    unittest.main()
