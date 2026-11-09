# Windows CLI entry helpers — unchanged source edition

`scripts/vdo.ps1` starts the existing `video_orchestrator.cli` module from this source checkout and forwards its arguments. `scripts/runtime-python.ps1` is the small shared interpreter selector it dot-sources. Both existing first-party scripts are included unchanged under the repository Apache-2.0 license; no new launcher framework, backend or dependency is added.

## Interpreter and environment selection

The selector uses the script's checkout root, not a workstation-specific absolute directory. If `.venv/Scripts/python.exe` exists, it selects that path, sets process `VIRTUAL_ENV` to `.venv`, and prepends `.venv/Scripts` only when that directory string is not already a member of the semicolon-separated process PATH. It does not normalize every equivalent spelling, install or activate a virtual environment, or validate that an existing path is a working executable.

Otherwise it uses `(Get-Command python -ErrorAction Stop).Source`. This is command lookup, not a verified interpreter-health check, and that fallback leaves an existing VIRTUAL_ENV unchanged. Aliases, functions, missing commands and incompatible Python/dependencies remain operator/runtime conditions. No virtual environment or PATH was inspected or changed during this source acceptance.

The helper is intended for dot-sourcing and sets variables and environment in the current PowerShell process. The CLI wrapper sets PYTHONPATH to the checkout's `src`, invokes the selected interpreter with `-m video_orchestrator.cli @args`, and ends with `exit $LASTEXITCODE`. Its existing stop-on-error behavior is retained. Native exit forwarding was read in source, not exercised in this batch; bootstrap errors do not guarantee a particular report or recovery behavior.

## Operator use

Use an existing suitable Windows Python environment with the CLI's required dependencies and any separately selected optional backend already available. Invoke `scripts/vdo.ps1` in a separate PowerShell process with the same arguments you have reviewed for the existing CLI. The conceptual command shape is `powershell.exe -NoProfile -File scripts/vdo.ps1` followed by those CLI arguments; this guide does not introduce new flags or an automatic operation. No invocation example was executed here. Consult the existing CLI documentation for the requested action.

Actual CLI actions can access files, network sources and configured backends depending on the supplied command. Source publication is not authorization to run downloads, authenticated requests, services or real customer content. This pair contains no account, secret, machine-specific runtime path or third-party payload. Operator runtime configuration and input/output rights remain separate.

## Actual verification and limits

Windows PowerShell 5.1 parsed both frozen sources without executing or dot-sourcing them: zero syntax errors. Full source/default/comment/relative-path/argument review and public CLI/package/license source-hash checks were completed. The 559-byte selector and 240-byte CLI wrapper match the local generic working snapshots exactly. No fixed historical Git blob is claimed for those snapshots.

No CLI, selected executable, download, service, model, browser, real data or backend was run. Interpreter selection, PATH precedence/idempotence, fallback behavior, native exit forwarding, installed-package behavior and cross-platform support are **unverified at runtime**. The existing original test file also checks other UI consumers outside this increment; it was not run, trimmed, weakened or represented as a passing suite here. Syntax and dependency-source checks do not substitute for those tests or for whole-workflow reconstruction.

Only these two entry sources and this guide are added; the package's already public CLI code is reused. No runtime binaries, credentials, private history, media or reference customer records are bundled. The root license describes this first-party increment, not a blanket permission to redistribute operator-supplied dependencies or downloaded material.
