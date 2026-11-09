# Windows UI lifecycle scripts

These five PowerShell scripts are the Windows launcher surface for the
video-download-orchestrator (VDO) Web UI. They are thin wrappers around the
Python backend: the wrappers own the managed receipt fields, the process
ownership checks, the local PortRecord note, and the scheduled-task start.
The Python backend remains responsible for the actual server and for the
business configuration it reads.

Apache-2.0 licensed VDO project source; this document describes the wrapper
behavior only.

## Scripts

| Script | Role |
| --- | --- |
| `scripts/start-ui.ps1` | Foreground one-shot launcher: verifies health, checks the port, writes the managed receipt, launches the hidden background server process, and verifies its health for up to 15 seconds. |
| `scripts/status-ui.ps1` | Prints the managed entrypoint status as JSON via the same interpreter helper. |
| `scripts/stop-ui.ps1` | Stops the process recorded in the managed receipt after confirming `-ConfirmStop`, root/endpoint/receipt identity, PID start time, executable path, and (when available) the CIM command line. |
| `scripts/serve-ui.ps1` | Foreground wrapper that loads the optional allowlist, generates the runtime config, validates it, and runs the web server. |
| `scripts/start-ui-scheduled.ps1` | Creates and runs a one-time Scheduled Task whose action calls `serve-ui.ps1`, then correlates the spawned VDO process with the receipt id and health endpoint. |

All scripts compute the project root from their own location
(`scripts/<name>.ps1` -> repository root), so the repository can be moved
without editing absolute paths.

## Interpreter helper

`scripts/runtime-python.ps1` is the existing shared helper. It dot-sources
from the launchers and sets `$VdoPython` to the project virtual environment
interpreter when present, otherwise to the `python` found on `PATH`.
`serve-ui.ps1` uses `& $VdoPython` for the config sync, the config check, and
the server, so the same interpreter is used for all three steps.

## Configuration and optional allowlist

The launchers read `config/video_orchestrator.json`; the config is required
and missing files fail the launcher. `serve-ui.ps1` loads the optional
allowlist after its config-presence check and before the config sync, the
config check, and the server calls; that ordering is preserved.
`VDO_TELEGRAM_ALLOWED_USER_IDS` is only populated from
`.vdo/openclaw-telegram-allowlist.txt` when the environment variable is unset
and the optional legacy allowlist file exists. That allowlist filename
remains current VDO authorization plumbing; it does not require an OpenClaw
runtime.

## PortRecord note

`start-ui.ps1` supports an operator-triggered PortRecord note through the
`-UpdatePortRecord` switch. The note target must be supplied explicitly with
`-PortRecordPath`; the launcher no longer contains a hardcoded private path.
`Add-PortRecord` returns without writing when the path is empty or does not
exist, and it never creates the target. The note is only attempted after the
managed process has passed startup verification and only when the operator
passed `-UpdatePortRecord`. No real knowledge-base or private note is
required or written by the default invocation.

## Exit codes and policy

`serve-ui.ps1` checks `$LASTEXITCODE` after the config sync and config check
and stops before the server on a nonzero result; the server's exit code is
forwarded to the caller. `start-ui-scheduled.ps1` checks `$LASTEXITCODE`
immediately after `schtasks /Create` and `schtasks /Run` and throws before any
further scheduler or process work. Metadata and display strings use ordinary
`powershell.exe -NoProfile` invocations; the launchers no longer embed
`-ExecutionPolicy Bypass` and never change machine policy.

## Verification status

This document accompanies a candidate source change plus limited static
evidence, not completed runtime verification. All eight static checks in
`tests/test_ui_service_scripts.py` passed, and a nonexecuting Windows
PowerShell 5.1 parser check found no syntax errors in the five launcher
scripts. The synthetic suite has not passed: fixture setup failed with a
permission refusal while creating temporary test files, and under fail-fast
the first fixture control ended at that refusal, so no synthetic control
ran. The wrapper runtime controls and the bad-copy control therefore remain
unverified. The bounded retry ended at that refusal; runtime verification remains blocked.

The operator fail-fast command is:

```powershell
python -m unittest discover -s tests -p test_ui_launcher_portability.py -v -f
```

Real UI start/stop, scheduled-task creation, process inspection or stopping,
HTTP health calls, and any knowledge-base write remain outside this task and
were not performed.

## Synthetic verification boundary

`tests/test_ui_launcher_portability.py` is an offline stdlib `unittest` file
intended to parse the five launcher scripts nonexecutingly with Windows
PowerShell 5.1 (`Parser.ParseFile`) and then exercise copied fixtures in a
temporary directory:

- the actual `Add-PortRecord` function is extracted through the PowerShell AST
  and checked for empty, missing, and supplied explicit paths;
- a copied `serve-ui.ps1` fixture runs with a synthetic `runtime-python.ps1`
  stand-in that only records calls and sets `$LASTEXITCODE`, verifying sync
  failure, config failure, server-exit forwarding, and the success call order;
- a copied `start-ui-scheduled.ps1` fixture runs with stubbed `schtasks` and
  task-settings cmdlets, verifying that create/run failures stop before
  settings, process inspection, health checks, and receipt writes.

A completed passing run remains pending: fixture setup for these controls
currently fails with a permission refusal while creating the temporary
fixture files, so the fixture controls have not produced a pass.

These are wrapper control tests. They do not prove interpreter selection,
real process ownership, real service health, real scheduled-task behavior,
schema or business-config validity, cross-midnight timing, or any network or
production behavior. Nothing here starts the UI or creates a real scheduled
task.

## Manual operation (operator)

```powershell
powershell.exe -NoProfile -File scripts\start-ui.ps1
powershell.exe -NoProfile -File scripts\status-ui.ps1
powershell.exe -NoProfile -File scripts\stop-ui.ps1 -ConfirmStop
```

The scheduled launcher is an alternative start path:

```powershell
powershell.exe -NoProfile -File scripts\start-ui-scheduled.ps1
```

The scheduled path creates a one-time task with an execution time limit of
zero and starts it immediately; the real scheduler behavior and the
cross-midnight case remain unverified by the synthetic tests.
