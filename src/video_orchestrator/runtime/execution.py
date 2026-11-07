from __future__ import annotations

from pathlib import Path
from typing import Any

from video_orchestrator.runtime.backend_recovery import backend_fallback_decision
from video_orchestrator.runtime.artifact_lifecycle import media_operation_preflight
from video_orchestrator.runtime.config import hard_timeout_seconds as configured_hard_timeout_seconds
from video_orchestrator.runtime.config import idle_timeout_seconds as configured_idle_timeout_seconds
from video_orchestrator.runtime.logs import write_task_attempts_log
from video_orchestrator.runtime.paths import report_dir
from video_orchestrator.runtime.quality import build_quality_review
from video_orchestrator.runtime.quality import classify_backend_error
from video_orchestrator.runtime.quality import discover_archive_files
from video_orchestrator.runtime.quality import discover_output_files
from video_orchestrator.runtime.quality import parse_backend_stdout_json
from video_orchestrator.runtime.quality import probe_media_files
from video_orchestrator.runtime.quality import write_quality_review
from video_orchestrator.runtime.redaction import redact_attempts
from video_orchestrator.runtime.redaction import redact_command
from video_orchestrator.runtime.reports import write_task_report
from video_orchestrator.runtime.subprocess_runner import run_command
from video_orchestrator.runtime.task_store import TaskStore
from video_orchestrator.runtime.site_adapters import match_site_adapter
from video_orchestrator.services import plan_download


def execute_manifest(
    *,
    store: TaskStore,
    manifest: dict[str, Any],
    artifact_base_dir: Path,
    manifest_path: Path,
    extra_payload: dict[str, Any] | None = None,
    hard_timeout: int | None = None,
    idle_timeout: int | None = None,
) -> dict[str, Any]:
    preflight = _download_space_preflight(manifest, artifact_base_dir)
    preflight_status = str(preflight.get("status") or "blocked_preflight_invalid")
    preflight_blocked = preflight.get("allowed") is False or preflight_status.startswith("blocked")
    if preflight_blocked:
        return {
            "service": "video-download-orchestrator",
            "status": preflight_status,
            "will_download": False,
            "selected_backend": str(manifest["selected_backend"]),
            "task": None,
            "artifacts": {"manifest_path": str(manifest_path)},
            "preflight": preflight,
            "next_action": preflight.get("next_action"),
        }
    task_id = store.create_task(str(manifest["url"]), str(manifest["selected_backend"]))
    attempts = []
    attempt_logs = []
    result = None
    selected_backend = str(manifest["selected_backend"])
    fallback_used = False
    route_backends = [selected_backend, *manifest.get("fallback_backends", [])]
    resolved_hard_timeout = hard_timeout if hard_timeout is not None else configured_hard_timeout_seconds()
    resolved_idle_timeout = idle_timeout if idle_timeout is not None else configured_idle_timeout_seconds()

    for index, backend in enumerate(route_backends):
        headers = manifest.get("request_headers") if isinstance(manifest.get("request_headers"), dict) else {}
        ytdlp_options = manifest.get("yt_dlp_options") if isinstance(manifest.get("yt_dlp_options"), dict) else {}
        command = (
            list(manifest["command"])
            if index == 0
            else list(
                plan_download(
                    str(manifest["url"]),
                    output_dir=artifact_base_dir,
                    backend=backend,
                    headers=headers,
                    ytdlp_options=ytdlp_options,
                )["command"]
            )
        )
        fallback_reason = None if index == 0 else f"previous backend failed: {attempts[-1]['backend']}"
        result = run_command(
            command,
            hard_timeout_seconds=resolved_hard_timeout,
            idle_timeout_seconds=resolved_idle_timeout,
        )
        error_info = classify_backend_error(result.stderr) or {}
        fallback_decision = backend_fallback_decision(
            url=str(manifest["url"]),
            error_info=error_info,
            remaining_backends=route_backends[index + 1 :],
        )
        attempt_logs.append(
            {
                "backend": backend,
                "command": command,
                "fallback_reason": fallback_reason,
                "result": result,
            }
        )
        attempts.append(
            {
                "backend": backend,
                "command": redact_command(command),
                "returncode": result.returncode,
                **error_info,
                "fallback_reason": fallback_reason,
                "timeout": result.timeout_kind,
                "timed_out": result.timed_out,
                "timeout_kind": result.timeout_kind,
                "duration_seconds": result.duration_seconds,
                "hard_timeout_seconds": resolved_hard_timeout,
                "idle_timeout_seconds": resolved_idle_timeout,
                "fallback_decision": fallback_decision,
            }
        )
        if result.returncode == 0:
            selected_backend = backend
            fallback_used = index > 0
            break
        if not fallback_decision["continue"]:
            break

    assert result is not None
    if result.returncode == 0:
        store.mark_finished(task_id, result.returncode)
        status = "finished"
    else:
        store.mark_failed(task_id, result.returncode)
        status = "failed"

    task_log_path = write_task_attempts_log(
        artifact_base_dir,
        task_id=task_id,
        attempts=attempt_logs,
        idle_timeout=resolved_idle_timeout,
        hard_timeout=resolved_hard_timeout,
    )

    backend_result = parse_backend_stdout_json(result.stdout)
    output_files = discover_output_files(artifact_base_dir, backend_result)
    archive_files = discover_archive_files(artifact_base_dir)
    ffprobe_results = probe_media_files(output_files)
    task_report_dir = report_dir(artifact_base_dir, task_id)
    review = build_quality_review(
        status="ok" if result.returncode == 0 else "failed",
        output_files=output_files,
        stderr=result.stderr,
        fallback_used=fallback_used,
        backend_result=backend_result,
        ffprobe_results=ffprobe_results,
        attempts=redact_attempts(attempts),
        sniffed_media=manifest.get("sniffed_media") if isinstance(manifest.get("sniffed_media"), dict) else None,
        archive_files=archive_files,
        archive_expected=selected_backend in {"yt-dlp", "yt-dlp-api"},
    )
    review_path = write_quality_review(task_report_dir, review)
    report_path = write_task_report(
        artifact_base_dir,
        {
            "task_id": task_id,
            "url": manifest["url"],
            "selected_backend": selected_backend,
            "status": status,
            "command": redact_command(list(result.args)),
            "attempts": redact_attempts(attempts),
            "output_dir": manifest["output_dir"],
            "log_path": str(task_log_path),
            "review_checklist_path": str(review_path),
            "backend_result": backend_result,
            "ffprobe": ffprobe_results,
            "archive_files": [path.as_posix() for path in archive_files],
            "sniffed_media": manifest.get("sniffed_media"),
            "quality_status": review["quality_status"],
            "quality_label": review["quality_label"],
            "transport_status": review["transport_status"],
            "transport_label": review["transport_label"],
            "transport_next_action": review["transport_next_action"],
            "media_integrity_status": review["media_integrity_status"],
            "media_integrity_label": review["media_integrity_label"],
            "media_integrity_next_action": review["media_integrity_next_action"],
            "timeout": result.timeout_kind,
            "next_action": review["quality_next_action"],
        },
    )
    store.add_artifact(task_id, "manifest", manifest_path)
    store.add_artifact(task_id, "log", task_log_path)
    store.add_artifact(task_id, "report", report_path)
    store.add_artifact(task_id, "review_checklist", review_path)
    payload = {
        "service": "video-download-orchestrator",
        "status": status,
        "selected_backend": selected_backend,
        "quality_status": review["quality_status"],
        "quality_label": review["quality_label"],
        "transport_status": review["transport_status"],
        "transport_label": review["transport_label"],
        "transport_next_action": review["transport_next_action"],
        "media_integrity_status": review["media_integrity_status"],
        "media_integrity_label": review["media_integrity_label"],
        "media_integrity_next_action": review["media_integrity_next_action"],
        "next_action": review["quality_next_action"],
        "attempts": redact_attempts(attempts),
        "task": store.get_task(task_id),
        "artifacts": {
            "manifest_path": str(manifest_path),
            "log_path": str(task_log_path),
            "report_path": str(report_path),
            "review_checklist_path": str(review_path),
        },
    }
    adapter = match_site_adapter(str(manifest["url"]))
    if status == "failed" and adapter is not None and adapter.fallback_mode == "browser_sniffing":
        stopped_attempt = next(
            (
                attempt
                for attempt in reversed(attempts)
                if isinstance(attempt.get("fallback_decision"), dict)
                and attempt["fallback_decision"].get("status") == "needs_browser_sniffing"
            ),
            None,
        )
        payload["recovery"] = {
            "status": "needs_browser_sniffing",
            "mode": "browser_sniffing",
            "site_adapter": adapter.name,
            "requires_cookie_export": False,
            "fallback_stopped": stopped_attempt is not None,
            "skipped_backends": (stopped_attempt or {}).get("fallback_decision", {}).get("skipped_backends", []),
            "next_action": (stopped_attempt or {}).get("fallback_decision", {}).get("next_action")
            or "请在当前登录浏览器打开原页面并播放几秒，系统将捕获新的主视频请求后继续。",
        }
    if extra_payload:
        payload.update(extra_payload)
    return payload


def _download_space_preflight(manifest: dict[str, Any], artifact_base_dir: Path) -> dict[str, Any]:
    expected_duration = manifest.get("expected_duration_seconds")
    estimated_output = manifest.get("estimated_output_bytes")
    estimate_source = "manifest.estimated_output_bytes" if estimated_output is not None else None
    sniffed_media = manifest.get("sniffed_media") if isinstance(manifest.get("sniffed_media"), dict) else {}
    completeness = (
        sniffed_media.get("media_completeness")
        if isinstance(sniffed_media.get("media_completeness"), dict)
        else sniffed_media.get("completeness")
        if isinstance(sniffed_media.get("completeness"), dict)
        else {}
    )
    if expected_duration is None:
        expected_duration = (
            sniffed_media.get("expected_duration_seconds")
            or sniffed_media.get("duration_seconds")
            or completeness.get("expected_duration_seconds")
            or completeness.get("duration_seconds")
        )
    if estimated_output is None:
        for source, value in (
            ("sniffed_media.expected_size_bytes", sniffed_media.get("expected_size_bytes")),
            ("sniffed_media.completeness.expected_size_bytes", completeness.get("expected_size_bytes")),
            ("sniffed_media.content_length", sniffed_media.get("content_length")),
            ("sniffed_media.completeness.content_length", completeness.get("content_length")),
        ):
            if value is not None:
                estimated_output = value
                estimate_source = source
                break
    result = media_operation_preflight(
        artifact_base_dir,
        operation="download",
        expected_duration_seconds=float(expected_duration) if expected_duration is not None else None,
        estimated_output_bytes=estimated_output,
    )
    result["estimated_output_source"] = estimate_source
    return result
