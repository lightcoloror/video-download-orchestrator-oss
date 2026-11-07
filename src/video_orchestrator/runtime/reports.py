from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from video_orchestrator.runtime.redaction import redact_attempts
from video_orchestrator.runtime.redaction import redact_command
from video_orchestrator.runtime.redaction import redact_sniffed_media
from video_orchestrator.runtime.redaction import redact_url


def write_task_report(base_dir: Path, summary: dict[str, Any]) -> Path:
    report_dir = _report_dir(base_dir, summary)
    report_dir.mkdir(parents=True, exist_ok=True)

    normalized = {
        "task_id": str(summary.get("task_id", "task")),
        "url": redact_url(summary["url"]) if isinstance(summary.get("url"), str) else summary.get("url"),
        "selected_backend": summary.get("selected_backend"),
        "status": summary.get("status", "unknown"),
        "command": redact_command(summary.get("command", [])),
        "attempts": redact_attempts(summary.get("attempts", [])),
        "output_dir": summary.get("output_dir"),
        "log_path": summary.get("log_path"),
        "review_checklist_path": summary.get("review_checklist_path"),
        "backend_result": summary.get("backend_result") or {},
        "ffprobe": summary.get("ffprobe") or [],
        "archive_files": summary.get("archive_files") or [],
        "sniffed_media": redact_sniffed_media(summary["sniffed_media"])
        if isinstance(summary.get("sniffed_media"), dict) and summary.get("sniffed_media")
        else {},
        "quality_status": summary.get("quality_status"),
        "quality_label": summary.get("quality_label"),
        "transport_status": summary.get("transport_status"),
        "transport_label": summary.get("transport_label"),
        "transport_next_action": summary.get("transport_next_action"),
        "media_integrity_status": summary.get("media_integrity_status"),
        "media_integrity_label": summary.get("media_integrity_label"),
        "media_integrity_next_action": summary.get("media_integrity_next_action"),
        "started_at": summary.get("started_at"),
        "finished_at": summary.get("finished_at"),
        "timeout": summary.get("timeout"),
        "next_action": summary.get("next_action", _default_next_action(summary)),
        "written_at": datetime.now(UTC).isoformat(),
    }

    summary_path = report_dir / "summary.json"
    summary_path.write_text(
        json.dumps(normalized, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )

    report_path = report_dir / "report.md"
    report_path.write_text(_render_report(normalized), encoding="utf-8")
    return report_path


def render_review_package_summary(
    *,
    task: dict[str, Any],
    verdict: str,
    artifacts: dict[str, str],
    next_action: str,
    handoff: dict[str, Any] | None = None,
) -> str:
    lines = [
        "# Task Review Package",
        "",
        f"- Task ID: {task.get('id')}",
        f"- URL: {redact_url(str(task.get('url')))}",
        f"- Backend: {task.get('backend')}",
        f"- Status: {task.get('status')}",
        f"- Verdict: {verdict}",
        "",
        "## Artifacts",
        "",
    ]
    if artifacts:
        for kind, path in sorted(artifacts.items()):
            lines.append(f"- {kind}: {Path(path).name}")
    else:
        lines.append("- No artifacts recorded.")
    if handoff:
        recommended_ui = handoff.get("recommended_ui") if isinstance(handoff.get("recommended_ui"), dict) else {}
        commands = handoff.get("commands") if isinstance(handoff.get("commands"), dict) else {}
        lines.extend(
            [
                "",
                "## Handoff",
                "",
                f"- Recommended UI: {recommended_ui.get('name')}",
                f"- Next action: {handoff.get('next_action')}",
            ]
        )
        for label, command in sorted(commands.items()):
            label_text = "Explicit execution command" if label == "cli_execute" else label
            lines.append(f"- {label_text}: `{command}`")
    lines.extend(["", "## Next Action", "", str(next_action), ""])
    return "\n".join(lines)


def _report_dir(base_dir: Path, summary: dict[str, Any]) -> Path:
    if base_dir.name == ".vdo":
        root = base_dir
    else:
        root = base_dir / ".vdo"
    task_id = str(summary.get("task_id", "task"))
    return root / "reports" / task_id


def _default_next_action(summary: dict[str, Any]) -> str:
    if summary.get("status") in {"ok", "finished"}:
        return "Review output files and review-checklist.json before accepting."
    return "Inspect report.md, summary.json, and backend logs before rerun."


def _render_report(summary: dict[str, Any]) -> str:
    lines = [
        "# Video Download Task Report",
        "",
        f"- Task ID: {summary['task_id']}",
        f"- URL: {summary.get('url')}",
        f"- Status: {summary.get('status')}",
        f"- Selected backend: {summary.get('selected_backend')}",
        f"- Output directory: {summary.get('output_dir')}",
        f"- Log path: {summary.get('log_path')}",
        f"- Review checklist: {summary.get('review_checklist_path')}",
        f"- Started at: {summary.get('started_at')}",
        f"- Finished at: {summary.get('finished_at')}",
        f"- Timeout: {summary.get('timeout')}",
        f"- Legacy quality status: {summary.get('quality_status')}",
        f"- Transport status: {summary.get('transport_status')}",
        f"- Media integrity status: {summary.get('media_integrity_status')}",
        "",
        "## Command",
        "",
        "```text",
        " ".join(map(str, summary.get("command") or [])),
        "```",
        "",
        "## Attempts",
        "",
    ]

    attempts = summary.get("attempts") or []
    if not attempts:
        lines.append("- No backend attempts recorded.")
    else:
        for index, attempt in enumerate(attempts, start=1):
            lines.extend(
                [
                    f"### Attempt {index}",
                    "",
                    f"- Backend: {attempt.get('backend')}",
                    f"- Return code: {attempt.get('returncode')}",
                    f"- Fallback reason: {attempt.get('fallback_reason')}",
                    f"- Timeout: {attempt.get('timeout')}",
                    f"- Duration seconds: {attempt.get('duration_seconds')}",
                    f"- Hard timeout seconds: {attempt.get('hard_timeout_seconds')}",
                    f"- Idle timeout seconds: {attempt.get('idle_timeout_seconds')}",
                    "",
                ]
            )

    backend_result = summary.get("backend_result") or {}
    if backend_result:
        lines.extend(
            [
                "## Backend Result",
                "",
                f"- Output file: {backend_result.get('output_file')}",
                f"- Bytes: {backend_result.get('bytes')}",
                f"- Size matches page: {backend_result.get('size_matches_page')}",
                "",
            ]
        )

    sniffed_media = summary.get("sniffed_media") or {}
    if sniffed_media:
        completeness = sniffed_media.get("completeness") if isinstance(sniffed_media.get("completeness"), dict) else {}
        lines.extend(
            [
                "## Browser Sniffed Capture",
                "",
                f"- Media type: {sniffed_media.get('media_type')}",
                f"- Media kind: {sniffed_media.get('media_kind')}",
                f"- Category: {sniffed_media.get('category')}",
                f"- Quality score: {sniffed_media.get('quality_score')}",
                f"- Candidate reason: {sniffed_media.get('download_candidate_reason')}",
                f"- Completeness: {completeness.get('label')}",
                f"- Completeness next action: {completeness.get('next_action')}",
                f"- Content length: {sniffed_media.get('content_length')}",
                f"- Content range: {sniffed_media.get('content_range')}",
                "",
            ]
        )

    ffprobe = summary.get("ffprobe") or []
    if ffprobe:
        lines.extend(["## FFprobe", ""])
        for item in ffprobe:
            metadata = item.get("metadata") or {}
            fmt = metadata.get("format") or {}
            lines.extend(
                [
                    f"- File: {item.get('path')}",
                    f"  Return code: {item.get('returncode')}",
                    f"  Format: {fmt.get('format_name')}",
                    f"  Duration: {fmt.get('duration')}",
                    f"  Size: {fmt.get('size')}",
                ]
            )
        lines.append("")

    archive_files = summary.get("archive_files") or []
    if archive_files:
        lines.extend(["## Archive Sidecars", ""])
        for path in archive_files:
            lines.append(f"- {path}")
        lines.append("")

    lines.extend(
        [
            "## Next Action",
            "",
            str(summary.get("next_action")),
            "",
        ]
    )
    return "\n".join(lines)
