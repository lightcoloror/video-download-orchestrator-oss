# Entry Source Contract Regressions Reference Guide

Destination: `docs/entry-source-contract-regressions.md`
License: Apache-2.0

## 1. Purpose
This document provides reference documentation for four first-party regression test methods across two test suites:
- `tests/test_runtime_python_launcher.py` (2 methods)
- `tests/test_upstream_source_contract.py` (2 methods)

These tests define regression boundaries for Windows desktop launcher scripts, virtual environment interpreter selection, external UI handoff definitions, and upstream downloader adapter metadata. The source is preserved as an Apache-2.0 reference specification; this guide documents existing contracts and known source-level mismatches against public base `6132803` without claiming test execution passes or turnkey readiness.

## 2. Two-File Specification Table (4 Methods)

| Source File | Test Method Name | Source Assertions (Not Executed) | Decorators & Preconditions | Execution Nature & Side Effects |
|---|---|---|---|---|
| `tests/test_runtime_python_launcher.py` | `test_launcher_prefers_project_interpreter_and_backend_path` | • Verifies that `runtime-python.ps1` selects a local `.venv/Scripts/python.exe` over system Python.<br>• Verifies that the `.venv/Scripts` directory is prepended to `$env:PATH`. | `@pytest.mark.skipif(os.name != "nt", reason="Windows desktop launcher")`<br>Requires `pwsh` or `powershell` on PATH. | **Active child process**: Copies `runtime-python.ps1` to temporary directory, creates empty synthetic `python.exe` placeholder (never run), and executes `powershell -NoProfile -Command` to dot-source the script and parse JSON output. Modifies child process environment. |
| `tests/test_runtime_python_launcher.py` | `test_start_and_status_share_runtime_selector_and_check_start_failures` | • Asserts `runtime-python.ps1` is referenced in both `start-ui.ps1` and `status-ui.ps1`.<br>• Asserts `start-ui.ps1` calls `-FilePath $VdoPython`.<br>• Asserts `status-ui.ps1` invokes `& $VdoPython -m video_orchestrator.cli`.<br>• Asserts `start-mediago-client.ps1` contains error check `if ($LASTEXITCODE -ne 0)`. | None | **Static file read**: Reads UTF-8 text with optional BOM of all three launcher scripts from `scripts/`. Fails if any script is absent. |
| `tests/test_upstream_source_contract.py` | `test_mediago_current_source_keeps_private_checkout_and_handoff_contract` | • Verifies MediaGo external UI specification defines source URL `https://github.com/mediago-dev/mediago`.<br>• Verifies `integration_mode` is `external_desktop_ui_http_handoff` and handoff modes include `http_api_import`.<br>• Verifies directory basename is `mediago` if `local_path` is resolved. | None | **In-memory inspection with filesystem checks**: Calls `get_external_ui_status(check=False)`. While `check=False` disables executable discovery, it still queries `Path.exists` for configured or relative source paths. |
| `tests/test_upstream_source_contract.py` | `test_gallery_source_is_codeberg_not_stale_or_incorrect_github_mirror` | • Verifies `gallery-dl` adapter references Codeberg URL expected by this test `https://codeberg.org/mikf/gallery-dl`.<br>• Verifies command name is `gallery-dl` with fallback `yt-dlp`.<br>• Asserts `build_gallery_dl_command` constructs correct CLI argument list. | None | **Pure in-memory contract**: Inspects `get_backend_registry()["gallery-dl"]` and constructs argument list. Performs no network operations or external downloads. |

## 3. Fixed-Base Mismatch Table (Expected vs. Actual Base 6132803)

The existing test expectations represent target contracts that diverge from the static source definitions currently present on public base `6132803`:

| Contract Component | Target Expected in Test | Actual in Public Base 6132803 | Source-Level Incompatibility Assessment |
|---|---|---|---|
| **MediaGo Project URL** (`external_uis.py`) | `https://github.com/mediago-dev/mediago` | `https://github.com/caorushizi/mediago` | **String Mismatch**: The test expects the mediago-dev URL; the fixed base records the caorushizi URL. This is a comparison of source strings, not verified migration chronology, live ownership, URL correctness or license clearance. |
| **gallery-dl Reference URL** (Backend Registry) | `https://codeberg.org/mikf/gallery-dl` | `https://github.com/mikf123/gallery-dl` | **String Mismatch**: The test expects the Codeberg URL, while the base records a different GitHub URL. The test name expresses an expectation; this increment has not verified either upstream location, migration, mirror status or current ownership. No test network request was run. |
| **MediaGo Launcher Script** (`scripts/start-mediago-client.ps1`) | File exists and contains exit-code check | File absent on base `6132803` | **Missing File Dependency**: `start-mediago-client.ps1` exists as a separate pending local candidate (`b49`) and is not approved or published on base `6132803`. The source unconditionally reads this missing path, a predicted dependency failure if executed against this exact base; it is not an observed test failure in this increment. |

## 4. Dependencies and Pending Launcher Architecture
- **Launcher Scripts**: Public base `6132803` supplies `scripts/runtime-python.ps1`, `scripts/start-ui.ps1`, and `scripts/status-ui.ps1`. The additional script, `scripts/start-mediago-client.ps1`, is pending review (`b49`) and must be supplied for full script-set consistency.
- **Python Backend Modules**: Upstream contract tests import `video_orchestrator.backends.base`, `video_orchestrator.backends.gallery_dl`, and `video_orchestrator.external_uis`. Compatibility with Pydantic and backend loaders would be exercised if these tests ran; it has not been evaluated here.
- **Path Resolution Semantics**: In `get_external_ui_status(check=False)`, helper functions evaluate path presence via `_path_if_exists` and `Path.exists`, checking environment variable `VDO_SOURCE_REVIEW_ROOT` or current working directory relative paths. This is not zero-I/O or an isolated sandbox.

## 5. Verification Scope: Static Properties vs. Unexecuted Runtimes
- **Static Verification Performed**: Static AST parsing verified full structural equality, function names, skip decorators, and assertions across all 4 test methods.
- **Unexecuted Operations**: **0 of the 4 pytest methods were executed in this increment.** Specifically:
  - No child PowerShell runtime test or backend process was launched by these tests.
  - No temporary directories were created or cleaned up.
  - No backend planning, media downloads, network requests, or UI launches took place.
  - No synthetic `python.exe` placeholders were generated.
- **Naming Context**: Test function names containing `private_checkout` refer to external application integration modes (HTTP API handoff), not the inclusion or distribution of private repositories, client checkouts, or proprietary data.

## 6. Source Privacy, Licensing, and Runtime Conditions
- **License**: All first-party test code and documentation are distributed under Apache-2.0.
- **Third-Party Upstream Boundaries**: Generic URLs pointing to MediaGo and gallery-dl describe expected project references; they do not redistribute upstream code or certify upstream license terms.
- **Runtime Environment**:
  - `test_launcher_prefers_project_interpreter_and_backend_path` is explicitly limited to Windows NT environments with PowerShell available.
  - Dot-sourcing `runtime-python.ps1` alters process-level environment variables (`$VdoPython`, `$env:PATH`). It does not provide OS-level process isolation.
- **No Turnkey Execution**: Because pending client scripts and target URL definitions diverge from base `6132803`, no turnkey execution command is provided. Test pass claims are withheld pending formal execution in an authorized environment.
