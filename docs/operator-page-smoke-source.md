# VDO Operator & Page-Link Smoke Sources — Review Guide

Source-only reference note. No smoke logic was executed.

## Purpose

This note covers two complete first-party PowerShell working snapshots (public copies normalize line endings to LF only) from a video-download orchestrator (VDO) acceptance path:

- batch retryable planning through the operator CLI/API;
- page-capture link normalization through the page-link smoke;
- diagnostics, handoff previews, review packaging, and a synthetic finished-task control.

It is a manual source reference, not a runnable local test or a new framework.

## Two files and their dependency chain

- `scripts/operator-smoke.ps1` (179 lines, 8632 bytes) — top-level operator batch smoke. After checking API health it invokes `scripts/smoke-page-link.ps1` (`-StartupWaitSeconds`).
- `scripts/smoke-page-link.ps1` (94 lines, 3458 bytes) — page-link smoke; can also be invoked directly.

Both derive the project root from their own script location (two directories up), require `config/video_orchestrator.json`, build the API base URL from `web.scheme` / `web.host` / `web.port`, and always resolve runtime.state_dir before selecting the smoke output base. TEST_TMP_ROOT overrides that selected base afterwards; it does not bypass the runtime config read. Both reference start-ui.ps1/stop-ui.ps1 and the CLI; the operator script additionally references TaskStore. The fixed public base supplies `scripts/start-ui.ps1`, `scripts/stop-ui.ps1`, `src/video_orchestrator/cli.py`, and `src/video_orchestrator/runtime/task_store.py` (declared by the owner; those files are outside the reviewed pair and were not read).

## Actual effects if executed

These are factual effects of the supplied code, not a recommendation to run it:

- **Configured API may be remote.** The base URL comes from external runtime config; it is not restricted to localhost.
- **Services may be started/stopped.** If `/health` does not report `ok`, the script runs `start-ui.ps1 -NoBrowser`; in `finally` it runs `stop-ui.ps1 -ConfirmStop` when its StartedService flag is set after the start invocation. That flag does not independently prove successful startup or ownership of the process later stopped.
- **HTTP GET/POST.** GET `/health`, `/operator/status`, `/operator/tasks/retryable`, `/sniffed-media/diagnostics`, `/operator/handoff`, `/sniffed-media/latest`; POST `/sniffed-media/plan` with a JSON payload.
- **CLI usage.** `batch ... --execute --page-link-mode sniff`, `task resume ... --dry-run`, `task handoff`, `task create`, `task export-review`, `sniff-diagnostics`, `task retryable`, `operator-doctor`; and `download-page ... --dry-run` in the page-link script.
- **Files/manifests/state/SQLite.** Writes spec JSON, `report.md`, `review-checklist.json`, a generated helper Python file, manifests, review packages, state, and `tasks.sqlite` under `$TEST_TMP_ROOT/operator-smoke` (or the runtime state dir fallback) and `$TEST_TMP_ROOT/smoke-page-link`; sets `PYTHONPATH`/`PYTHONUTF8` for CLI calls.
- **Synthetic control only.** The generated Python calls `TaskStore.mark_finished` for a synthetic task with local report artifacts. That is control metadata, not evidence of a real download, media payload, or business result.

`TEST_TMP_ROOT` is an existing optional output root / config-state fallback; it is not OS isolation and does not make execution safe or hermetic.

## Source-only commands are NOT a quickstart

Nothing in the source is offered here as an executable quickstart. Running `operator-smoke.ps1`, invoking `start-ui.ps1`/`stop-ui.ps1`, calling the CLI with `--execute`, or issuing the HTTP requests presumes a project tree, runtime config, Python environment, backend service, network access, and permissions that were not provided or verified. A `--dry-run` flag on individual CLI calls does not make the whole script read-only.

## Checked / not run

**Checked (static):**

- Full contents of both scripts (179 and 94 lines).
- Owner facts and the owner-supplied SHA-256 values; byte sizes and line counts matched the owner records (8632 / 3458 bytes). Hashes were not independently recomputed, as hashing was outside the authorized read-only command set.
- Output boundaries and effect list above were derived from reading the code only.

**One authorized frozen parse attempt:**

- One separately supplied parse-only harness was attempted from the review task directory (timeout 60000 ms); that harness is not part of this public increment or an operator quickstart.
- The program uses `Parser.ParseFile` only and does not execute or dot-source the smoke bodies.
- Result: rejected at launch, before any parse output. Raw first error: `AuthorizationManager check failed.` (SecurityError, `UnauthorizedAccess`), exit code 1. The parser step was stopped; no retry, alternate interpreter, or ExecutionPolicy change was attempted.
- **Syntax is therefore explicitly unverified.**

**Not run:** both smoke scripts; `start-ui.ps1`/`stop-ui.ps1`; any CLI/Python module; any HTTP request to the configured host; any SQLite/state/manifest/review-package write; any backend, service, network, or download.

## Known limitations, rights, privacy

- **Limits.** Behavior depends on an external config file shape, service start/stop semantics, CLI/API contracts, and the platform's PowerShell policy; none were exercised or verified. The source contains native CLI invocations whose exit codes are mostly not explicitly checked, so a final printed `status: ok` does not prove every command succeeded; no claim is made that all native exit checks exist. Service ownership and external permissions are unproven. A missing runtime alone is not a redistribution blocker, but it means this is not self-contained.
- **Rights.** The owner declares the source first-party Apache-2.0; license text was not independently reviewed. No upstream distribution rights and no real-download clearance are established by this note. Publication and user approval remain separate.
- **Privacy.** No credentials, customer/health/media payload, or third-party copied code was observed in the supplied source. `example.com` / `cdn.example.com` URLs are generic test inputs and were left unchanged. No private history was reconstructed, and host-specific paths, task/chat/model identifiers are intentionally omitted from this public note.


## Owner qualification
Source byte sizes above describe the original reviewed snapshots; public files
only normalize CRLF to LF. Every source line and statement remains. The raw
metadata collection first had a non-terminating Get-Content pipeline binding
error and reported zero lines; a corrected authorized read-only count then
reported 179/94. The peer summary omitted that earlier metadata error; it is
retained in the Owner evidence, separate from the parser's first policy error.
Neither line counting nor unchanged-line comparison verifies syntax. After the
parser refusal, only authorized output/input metadata listing followed; there
was no alternate parser attempt, permission repair or smoke execution.
