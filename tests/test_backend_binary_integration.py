import os

import pytest

from video_orchestrator.backends.base import get_backend_specs
from video_orchestrator.runtime.subprocess_runner import command_exists
from video_orchestrator.runtime.subprocess_runner import run_command


pytestmark = pytest.mark.backend_integration


def _require_backend_integration_opt_in() -> None:
    if os.environ.get("VDO_RUN_BACKEND_INTEGRATION") != "1":
        pytest.skip("set VDO_RUN_BACKEND_INTEGRATION=1 to probe installed backend binaries")


def test_required_backend_binaries_are_available_when_opted_in():
    _require_backend_integration_opt_in()

    missing = [spec.name for spec in get_backend_specs() if spec.required and not command_exists(spec.command)]

    assert missing == []


def test_installed_backend_version_commands_exit_cleanly_when_opted_in():
    _require_backend_integration_opt_in()

    failures = []
    skipped_optional = []
    checked = []
    for spec in get_backend_specs():
        if not command_exists(spec.command):
            skipped_optional.append(spec.name)
            continue

        result = run_command(
            [spec.command, *spec.version_args],
            hard_timeout_seconds=20,
            idle_timeout_seconds=5,
        )
        checked.append(spec.name)
        if result.returncode != 0:
            failures.append(
                {
                    "backend": spec.name,
                    "command": [spec.command, *spec.version_args],
                    "returncode": result.returncode,
                    "stdout": result.stdout.strip(),
                    "stderr": result.stderr.strip(),
                    "timeout_kind": result.timeout_kind,
                }
            )

    assert checked
    assert failures == []
    assert all(name for name in skipped_optional)
