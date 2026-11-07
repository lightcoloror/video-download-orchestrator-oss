from __future__ import annotations

from pathlib import Path
from typing import Any

from video_orchestrator.external_uis import get_external_ui_status
from video_orchestrator.router import classify_url
from video_orchestrator.runtime.redaction import redact_url
from video_orchestrator.runtime.task_store import TaskStore


def build_url_handoff(url: str, *, output_dir: str = "downloads", capture_session: str | None = None) -> dict[str, Any]:
    route = classify_url(url)
    ui = _select_ui(route.preferred_backend, route.kind)
    redacted_url = redact_url(url)
    commands = {
        "cli_plan": f'python -m video_orchestrator.cli download "{redacted_url}" --dry-run --output-dir "{output_dir}" --format json',
        "cli_execute": f'python -m video_orchestrator.cli download "{redacted_url}" --output-dir "{output_dir}" --format json',
    }
    if route.kind in {"general", "feishu-page", "tencent-meeting"}:
        session = capture_session or "<session-id>"
        commands["page_sniff"] = (
            f'python -m video_orchestrator.cli download-page "{redacted_url}" '
            f'--capture-session "{session}" --output-dir "{output_dir}" --format json'
        )
    if route.kind == "manifest":
        commands["vsd_capture"] = f'python -m video_orchestrator.cli sniff "{redacted_url}" --format json'

    return {
        "service": "video-download-orchestrator",
        "status": "planned",
        "will_download": False,
        "route": route.model_dump(),
        "recommended_ui": ui,
        "commands": commands,
        "safety": {
            "requires_explicit_execution": True,
            "review_before_accept": True,
            "sensitive_values_redacted": True,
        },
        "next_action": _next_action(route.kind, str(ui["name"])),
    }


def build_task_handoff(task_id: int, *, db_path: Path | str, output_dir: str = "downloads") -> dict[str, Any]:
    store = TaskStore(Path(db_path).resolve())
    try:
        task = store.get_task(task_id)
    except KeyError:
        return {
            "service": "video-download-orchestrator",
            "status": "not_found",
            "will_download": False,
            "task_id": task_id,
            "next_action": "Check the task id and database path.",
        }

    payload = build_url_handoff(str(task["url"]), output_dir=output_dir)
    payload["task"] = task
    payload["review_first"] = task["status"] in {"finished", "accepted"}
    if task["status"] == "retryable" or task["backend"] == "browser-sniffer":
        payload["commands"]["task_resume_dry_run"] = (
            f"python -m video_orchestrator.cli task resume {task_id} "
            f'--db-path "{Path(db_path)}" --output-dir "{output_dir}" --dry-run --format json'
        )
        payload["next_action"] = "Open the source page, play the video with browser sniffer active, then run the task resume dry-run command."
    if task["status"] == "finished":
        payload["next_action"] = "Inspect report.md and review-checklist.json before accepting or re-running the task."
    return payload


def _select_ui(preferred_backend: str, kind: str) -> dict[str, Any]:
    uis = {ui.name: ui.model_dump() for ui in get_external_ui_status(check=False)}
    if preferred_backend == "aria2" or kind == "direct-transfer":
        return uis["motrix-next"]
    if preferred_backend == "n_m3u8dl_re" or kind == "manifest":
        return uis["hls-downloader"]
    if preferred_backend in {"yt-dlp-api", "yt-dlp"} or kind == "general":
        return uis["metube"]
    if preferred_backend in {"gallery-dl", "spotdl"}:
        return uis["gdownloader"]
    return {
        "name": "cli",
        "purpose": "Use the orchestrator CLI for site-specific or fallback routing.",
        "source_project": "local",
        "local_path": None,
        "integration_mode": "orchestrator_cli",
        "routes": [preferred_backend],
        "handoff_modes": ["copy_cli_command"],
        "status": "available",
        "launch_command": None,
        "start_hint": "Use the generated CLI command from the handoff payload.",
    }


def _next_action(kind: str, ui_name: str) -> str:
    if kind in {"feishu-page", "tencent-meeting"}:
        return "Try the site adapter via CLI, or use browser sniffer capture if the page requires browser-only state."
    if kind == "manifest":
        return "Use browser capture or the planned manifest backend; keep execution explicit."
    if kind == "direct-transfer":
        return f"Use {ui_name} as the external direct-transfer UI reference, or run the CLI dry-run first."
    return f"Use {ui_name} as the external UI reference, or run the CLI dry-run first."
