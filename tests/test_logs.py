from pathlib import Path

from video_orchestrator.runtime.logs import write_task_attempts_log
from video_orchestrator.runtime.logs import write_task_log
from video_orchestrator.runtime.subprocess_runner import CommandResult


def test_write_task_log_records_command_and_fallback_reason(tmp_path: Path):
    path = write_task_log(
        tmp_path,
        task_id=42,
        backend="yt-dlp",
        command=["yt-dlp", "https://example.com/video"],
        result=CommandResult(
            args=["yt-dlp", "https://example.com/video"],
            returncode=1,
            stdout="",
            stderr="warning: nope",
            timed_out=True,
            timeout_kind="idle",
            duration_seconds=300.1,
        ),
        fallback_reason="unsupported URL",
        idle_timeout=300,
        hard_timeout=7200,
    )

    content = path.read_text(encoding="utf-8")
    assert "yt-dlp https://example.com/video" in content
    assert "return_code: 1" in content
    assert "timed_out: True" in content
    assert "timeout_kind: idle" in content
    assert "idle_timeout: 300" in content
    assert "hard_timeout: 7200" in content
    assert "fallback_reason: unsupported URL" in content
    assert "warning: nope" in content


def test_write_task_attempts_log_keeps_all_attempt_stderr(tmp_path: Path):
    path = write_task_attempts_log(
        tmp_path,
        task_id=7,
        attempts=[
            {
                "backend": "yt-dlp-api",
                "command": ["python", "-m", "yt_dlp", "https://example.com"],
                "fallback_reason": None,
                "result": CommandResult(args=["python"], returncode=1, stdout="", stderr="primary stderr"),
            },
            {
                "backend": "lux",
                "command": ["lux", "https://example.com"],
                "fallback_reason": "previous backend failed: yt-dlp-api",
                "result": CommandResult(args=["lux"], returncode=127, stdout="", stderr="fallback missing"),
            },
        ],
    )

    content = path.read_text(encoding="utf-8")
    assert "## Attempt 1" in content
    assert "primary stderr" in content
    assert "## Attempt 2" in content
    assert "fallback missing" in content
