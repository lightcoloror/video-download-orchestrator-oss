from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from video_orchestrator.runtime.paths import log_path
from video_orchestrator.runtime.redaction import redact_command
from video_orchestrator.runtime.subprocess_runner import CommandResult


def write_task_log(
    output_dir: Path,
    *,
    task_id: int | str,
    backend: str,
    command: list[str],
    result: CommandResult,
    fallback_reason: str | None = None,
    idle_timeout: int | None = None,
    hard_timeout: int | None = None,
) -> Path:
    path = log_path(output_dir, task_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        _render_log(
            backend=backend,
            command=command,
            result=result,
            fallback_reason=fallback_reason,
            idle_timeout=idle_timeout,
            hard_timeout=hard_timeout,
        ),
        encoding="utf-8",
    )
    return path


def write_task_attempts_log(
    output_dir: Path,
    *,
    task_id: int | str,
    attempts: list[dict],
    idle_timeout: int | None = None,
    hard_timeout: int | None = None,
) -> Path:
    path = log_path(output_dir, task_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    sections = ["# Command Attempt Logs", ""]
    for index, attempt in enumerate(attempts, start=1):
        result = attempt["result"]
        sections.extend(
            [
                f"## Attempt {index}",
                "",
                _render_log(
                    backend=str(attempt.get("backend")),
                    command=list(attempt.get("command") or []),
                    result=result,
                    fallback_reason=attempt.get("fallback_reason"),
                    idle_timeout=idle_timeout,
                    hard_timeout=hard_timeout,
                ),
                "",
            ]
        )
    path.write_text("\n".join(sections), encoding="utf-8")
    return path


def _render_log(
    *,
    backend: str,
    command: list[str],
    result: CommandResult,
    fallback_reason: str | None,
    idle_timeout: int | None,
    hard_timeout: int | None,
) -> str:
    return "\n".join(
        [
            "# Command Log",
            "",
            "## metadata",
            "",
            f"written_at: {datetime.now(UTC).isoformat()}",
            f"backend: {backend}",
            f"command: {' '.join(map(str, redact_command(command)))}",
            f"return_code: {result.returncode}",
            f"timed_out: {result.timed_out}",
            f"timeout_kind: {result.timeout_kind}",
            f"duration_seconds: {result.duration_seconds}",
            f"fallback_reason: {fallback_reason}",
            f"idle_timeout: {idle_timeout}",
            f"hard_timeout: {hard_timeout}",
            "",
            "## stdout",
            "",
            result.stdout,
            "",
            "## stderr",
            "",
            result.stderr,
            "",
        ]
    )
