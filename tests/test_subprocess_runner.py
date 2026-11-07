import sys

from video_orchestrator.runtime.subprocess_runner import run_command


def test_run_command_captures_stdout():
    result = run_command([sys.executable, "-c", "print('ok')"])

    assert result.returncode == 0
    assert "ok" in result.stdout
    assert result.timed_out is False
    assert result.duration_seconds is not None


def test_run_command_hard_timeout_kills_process():
    result = run_command(
        [sys.executable, "-c", "import time; time.sleep(2)"],
        hard_timeout_seconds=1,
        idle_timeout_seconds=5,
    )

    assert result.returncode != 0
    assert result.timed_out is True
    assert result.timeout_kind == "hard"


def test_run_command_idle_timeout_kills_quiet_process():
    result = run_command(
        [sys.executable, "-c", "import time; time.sleep(2)"],
        hard_timeout_seconds=5,
        idle_timeout_seconds=1,
    )

    assert result.returncode != 0
    assert result.timed_out is True
    assert result.timeout_kind == "idle"
