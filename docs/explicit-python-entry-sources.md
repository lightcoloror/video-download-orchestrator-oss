# Explicit-Python Entry Sources Reference Guide

Destination: `docs/explicit-python-entry-sources.md`
License: Apache-2.0

## 1. Purpose
This document provides reference documentation for two first-party PowerShell entry scripts:
- `scripts/run-backend-integration-tests.ps1`
- `scripts/start-mcp.ps1`

Both scripts share a single architectural pattern: providing an explicit-Python entrypoint executed from the project root directory. Each wrapper resolves the repository root, switches the working directory, sets the process-level `PYTHONPATH` to `src`, executes a dedicated Python workload via `-m`, and propagates the resulting `$LASTEXITCODE`. They are distributed as lightweight, generic entrypoints under Apache-2.0 without bundling third-party binaries, local virtual environments, or proprietary configurations.

## 2. Two-File Specification Table

| Script Name | Parameters & Defaults | Target Invocation (`-m`) | Environment Overrides | Primary Operational Purpose |
|---|---|---|---|---|
| `scripts/run-backend-integration-tests.ps1` | `[string]$Python = "python"`<br>`[string[]]$PytestArgs = @("-q")` | `pytest tests\test_backend_binary_integration.py @PytestArgs` | `$env:PYTHONPATH = "<ProjectRoot>\src"`<br>`$env:VDO_RUN_BACKEND_INTEGRATION = "1"` | Explicitly activates opt-in integration tests that probe locally installed downloader backend binaries via registered version-argument commands (often --version; behavior is not exercised here). |
| `scripts/start-mcp.ps1` | `[string]$Python = "python"` | `video_orchestrator.mcp_server` | `$env:PYTHONPATH = "<ProjectRoot>\src"` | Launches the Model Context Protocol (MCP) server over standard I/O (`serve_stdio`) for planning, status reporting, and artifact management. |

## 3. Dependencies and Process Side Effects
- **Interpreter Selection**: Both wrappers declare a `$Python` parameter defaulting to `"python"`. The default python command is looked up using the caller's PATH; an explicit executable path supplied through the parameter is used as provided. Neither script contains automated virtual environment detection or shared runtime selectors (such as `runtime-python.ps1`). The operator must supply an explicit path if a specific interpreter or virtual environment is required.
- **Process State and Directory Mutations**:
  - Each script executes `Set-Location $ProjectRoot` and assigns `$env:PYTHONPATH = Join-Path $ProjectRoot "src"`.
  - When run interactively within a persistent PowerShell session, these changes modify the calling session's working directory and environment variables without restoration.
  - Neither script provides operating system-level process sandboxing or path confinement.
- **Backend Integration Test Wrapper (`run-backend-integration-tests.ps1`)**:
  - By setting `$env:VDO_RUN_BACKEND_INTEGRATION = "1"`, this script satisfies the test helper's environment opt-in. The backend_integration marker is separately declared in the test module and registered in pyproject.toml; the environment variable does not itself activate or select that marker. It does not perform neutral test discovery or run the full test suite.
  - The underlying test (`tests/test_backend_binary_integration.py`) verifies the presence of required backend tools and executes version commands (with a 20-second hard timeout and 5-second idle timeout). Missing binaries in that version loop are collected in skipped_optional; this is not an observed result or a per-binary pytest.skip event.
  - The script does not download or install backend binaries. Any extra arguments supplied via `$PytestArgs` pass directly to pytest and require operator review.
- **MCP Server Entrypoint (`start-mcp.ps1`)**:
  - Dispatches directly to `video_orchestrator.mcp_server`, which defaults to stdio communication (`serve_stdio`), reading newline-delimited JSON from stdin and writing JSON responses to stdout.
  - The wrapper contains no arguments for HTTP listeners, network ports, or web interfaces.
  - The MCP server exposes tools for download planning, task tracking, and artifact metadata and text read-back. Because planning routines may write manifests (`write_manifest=true`) and interact with local task databases, the server is **not** read-only. Setting `will_download=false` does not establish an OS sandbox or network boundary.

## 4. Verification Scope: Inspected Static Properties vs. Unexecuted Runtimes
- **Checked Static Properties**:
  - Inspected exact parameter declarations, error actions (`$ErrorActionPreference = "Stop"`), command formatting, and exit code forwarding.
  - Cross-referenced entrypoint targets against `pyproject.toml` (which declares `requires-python = ">=3.11"`, script target `vdo-mcp = "video_orchestrator.mcp_server:serve_stdio"`, and pytest markers).
  - Confirmed that backend binary test assertions enforce zero failures across checked binaries.
- **Explicitly Unexecuted Operations**:
  - **Zero scripts or tests were executed in this increment.**
  - No wrapper body or PowerShell parser was invoked. A previous parse-only launch was refused by AuthorizationManager; it was not retried or rerouted in this batch, and syntax remains unverified.
  - No Python workload was launched through these wrappers; no MCP session or pytest command was run. Host source preparation is separate from wrapper/runtime execution.
  - No external backend binaries (e.g., yt-dlp, gallery-dl) were probed or installed, and no download workflows were tested.
  - No temporary fixtures or task store databases were initialized.

## 5. Source Privacy, Rights, and Current Limitations
- **Licensing & Third-Party Boundaries**: The wrapper scripts are original first-party components licensed under Apache-2.0. Third-party backend utilities and Python libraries declared in `pyproject.toml` (such as Typer, Pydantic, yt-dlp) retain their upstream licenses. Distributing these wrappers conveys no upstream software licenses or pre-compiled binaries.
- **Data Privacy**: The scripts contain no hardcoded user paths, machine identities, authentication tokens, or client credentials.
- **Operational Reality**: Neither script is a self-contained turnkey installer. Successful execution requires an operator-maintained Python 3.11+ environment with declared project dependencies and any desired backend command-line tools already installed on the host. No quickstart commands or installation recipes are provided.

## Source preservation
The two complete existing scripts retain all 27 original lines and statements;
public copies normalize CRLF to LF only. Their original hashes and fixed-base
dependency hashes are recorded in the private review receipt. No interpreter
selection, pytest opt-in, argument forwarding or exit-code guard was changed.
