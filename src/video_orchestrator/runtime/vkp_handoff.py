from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from video_orchestrator.router import classify_url
from video_orchestrator.runtime.operator import artifact_map
from video_orchestrator.runtime.operator import operator_review_summary
from video_orchestrator.runtime.operator import read_json_artifact
from video_orchestrator.runtime.redaction import redact_url
from video_orchestrator.runtime.task_store import TaskStore


def build_vkp_handoff(
    task_id: int,
    *,
    db_path: Path | str = ".vdo/tasks.sqlite",
    output_dir: Path | str = "downloads/vkp-handoffs",
    require_accepted: bool = False,
    write_manifest: bool = True,
) -> dict[str, Any]:
    store = TaskStore(Path(db_path).resolve())
    try:
        task = store.get_task(task_id)
    except KeyError:
        return {
            "contract": "vdo_vkp_handoff_v1",
            "service": "video-download-orchestrator",
            "status": "not_found",
            "will_download": False,
            "execute": False,
            "task_id": task_id,
            "next_action": "未找到任务。请检查 task_id 和 db_path。",
        }

    artifacts = artifact_map(store.list_artifacts(task_id))
    review_summary = operator_review_summary(task, artifacts)
    review = review_summary["review"]
    summary = review_summary["summary"]
    manifest = read_json_artifact(artifacts.get("manifest"))
    source_url = str(task.get("url") or manifest.get("url") or "")
    route = manifest.get("route") if isinstance(manifest.get("route"), dict) else {}
    route_kind = str(route.get("kind") or classify_url(source_url).kind)
    selected_backend = str(manifest.get("selected_backend") or task.get("backend") or "")
    media_path = _find_media_path(review, summary)
    media_info = _media_info(media_path)
    accepted = str(task.get("status") or "") == "accepted"
    human_reviewed = accepted
    status = _handoff_status(task, media_info["media_exists"], require_accepted, accepted)

    payload: dict[str, Any] = {
        "contract": "vdo_vkp_handoff_v1",
        "service": "video-download-orchestrator",
        "will_download": False,
        "execute": False,
        "status": status,
        "created_at": datetime.now(UTC).isoformat(),
        "source": {
            "source_url": redact_url(source_url),
            "canonical_url": redact_url(str((route.get("url") if route else None) or manifest.get("url") or source_url)),
            "platform": _platform_label(source_url, route_kind),
            "page_title": _page_title(manifest, summary),
            "route_kind": route_kind,
            "selected_backend": selected_backend,
        },
        "download_task": {
            "task_id": task_id,
            "status": task.get("status"),
            "quality_status": review_summary["quality_status"],
            "quality_label": review_summary["quality_label"],
            "human_reviewed": human_reviewed,
            "accepted": accepted,
            "reason_labels": review_summary["reason_labels"],
            "recommended_actions": review_summary["recommended_actions"],
        },
        "media": media_info,
        "sidecars": _sidecars(artifacts, summary),
        "artifacts": {
            "manifest_path": artifacts.get("manifest"),
            "summary_path": artifacts.get("summary"),
            "report_path": artifacts.get("report"),
            "review_checklist_path": artifacts.get("review_checklist"),
        },
        "openclaw": _openclaw_audit(manifest, summary, review),
        "next_action": _next_action(status, review_summary["quality_status"], media_info["media_exists"], accepted),
    }
    payload["human_summary"] = _human_summary(payload)
    payload["vkp_ingest_suggestion"] = _vkp_ingest_suggestion(payload)

    if write_manifest:
        manifest_path = _write_handoff_manifest(payload, Path(output_dir), task_id)
        payload["handoff_manifest_path"] = str(manifest_path)
    return payload


def _handoff_status(task: dict[str, Any], media_exists: bool, require_accepted: bool, accepted: bool) -> str:
    task_status = str(task.get("status") or "")
    if require_accepted and not accepted:
        return "review_required"
    if task_status not in {"finished", "accepted"}:
        return "not_ready"
    if not media_exists:
        return "incomplete"
    return "planned"


def _find_media_path(review: dict[str, Any], summary: dict[str, Any]) -> str | None:
    backend_result = review.get("backend_result") if isinstance(review.get("backend_result"), dict) else {}
    for value in [backend_result.get("output_file"), summary.get("output_file")]:
        if isinstance(value, str) and value.strip():
            return value
    output_files = summary.get("output_files")
    if isinstance(output_files, list):
        for item in output_files:
            if isinstance(item, str) and item.strip():
                return item
            if isinstance(item, dict) and isinstance(item.get("path"), str) and item["path"].strip():
                return str(item["path"])
    checks = review.get("checks") if isinstance(review.get("checks"), list) else []
    for check in checks:
        files = check.get("files") if isinstance(check, dict) and isinstance(check.get("files"), list) else []
        for item in files:
            if isinstance(item, dict) and isinstance(item.get("path"), str) and item["path"].strip():
                return str(item["path"])
    return None


def _media_info(media_path: str | None) -> dict[str, Any]:
    if not media_path:
        return {
            "media_path": None,
            "media_exists": False,
            "content_type": None,
            "duration_seconds": None,
            "size_bytes": None,
        }
    path = Path(media_path)
    exists = path.exists() and path.is_file()
    return {
        "media_path": str(path),
        "media_exists": exists,
        "content_type": _content_type(path),
        "duration_seconds": None,
        "size_bytes": path.stat().st_size if exists else None,
    }


def _content_type(path: Path) -> str | None:
    suffix = path.suffix.lower()
    return {
        ".mp4": "video/mp4",
        ".m4v": "video/mp4",
        ".webm": "video/webm",
        ".mkv": "video/x-matroska",
        ".mov": "video/quicktime",
        ".mp3": "audio/mpeg",
        ".m4a": "audio/mp4",
        ".aac": "audio/aac",
        ".flac": "audio/flac",
        ".ogg": "audio/ogg",
        ".opus": "audio/opus",
        ".wav": "audio/wav",
        ".weba": "audio/webm",
    }.get(suffix)


def _sidecars(artifacts: dict[str, str], summary: dict[str, Any]) -> dict[str, Any]:
    return {
        "info_json_path": _first_path("info_json", artifacts, summary),
        "description_path": _first_path("description", artifacts, summary),
        "subtitle_paths": _path_list("subtitles", artifacts, summary),
    }


def _first_path(name: str, artifacts: dict[str, str], summary: dict[str, Any]) -> str | None:
    if artifacts.get(name):
        return artifacts[name]
    value = summary.get(name) or summary.get(f"{name}_path")
    return str(value) if isinstance(value, str) and value.strip() else None


def _path_list(name: str, artifacts: dict[str, str], summary: dict[str, Any]) -> list[str]:
    values: list[str] = []
    artifact_value = artifacts.get(name) or artifacts.get("subtitle") or artifacts.get("subtitles")
    if artifact_value:
        values.append(str(artifact_value))
    for key in [name, "subtitle_paths", "sidecar_subtitles"]:
        value = summary.get(key)
        if isinstance(value, list):
            values.extend(str(item) for item in value if item)
        elif isinstance(value, str) and value.strip():
            values.append(value)
    deduped: list[str] = []
    for value in values:
        if value not in deduped:
            deduped.append(value)
    return deduped


def _openclaw_audit(manifest: dict[str, Any], summary: dict[str, Any], review: dict[str, Any]) -> dict[str, Any]:
    candidates = [manifest.get("openclaw_approval"), summary.get("openclaw_approval"), review.get("openclaw_approval")]
    approval = next((item for item in candidates if isinstance(item, dict)), {})
    return {
        "approval_channel": approval.get("approval_channel") if approval else None,
        "actor_audit_present": bool(approval.get("actor_id")) if approval else False,
    }


def _page_title(manifest: dict[str, Any], summary: dict[str, Any]) -> str | None:
    for value in [
        manifest.get("title"),
        manifest.get("page_title"),
        summary.get("title"),
        summary.get("page_title"),
    ]:
        if isinstance(value, str) and value.strip():
            return value
    metadata = manifest.get("metadata_preview")
    if isinstance(metadata, dict) and isinstance(metadata.get("title"), str):
        return str(metadata["title"])
    return None


def _platform_label(url: str, route_kind: str) -> str:
    host = ""
    try:
        from urllib.parse import urlparse

        host = urlparse(url).netloc.lower()
    except Exception:
        host = ""
    if "youtube.com" in host or "youtu.be" in host:
        return "youtube"
    if "bilibili.com" in host or "b23.tv" in host:
        return "bilibili"
    if "xiaohongshu.com" in host or "xhslink.com" in host:
        return "xiaohongshu"
    if "douyin.com" in host or "ixigua.com" in host or "toutiao.com" in host:
        return "douyin"
    if "meeting.tencent.com" in host:
        return "tencent-meeting"
    if "feishu.cn" in host or "larksuite.com" in host:
        return "feishu"
    if route_kind:
        return route_kind
    return "unknown"


def _next_action(status: str, quality_status: str, media_exists: bool, accepted: bool) -> str:
    if status == "planned":
        if accepted:
            return "交接包已生成。可由人工把 media_path 交给 VKP openclaw-video-ingest。"
        return "交接包已生成，但任务尚未标记通过。请人工检查媒体文件和 review-checklist 后再交给 VKP。"
    if status == "review_required":
        return "任务尚未标记通过。请先人工检查并标记通过，再生成严格交接包。"
    if status == "incomplete" or not media_exists:
        return "未找到可交接的本地媒体文件。请检查 report.md、summary.json 和 review-checklist.json。"
    if quality_status == "fail":
        return "任务质量检查失败，不建议交给 VKP。请先恢复或重新执行。"
    return "任务尚未准备好交给 VKP。请先完成下载和人工 review。"


def _human_summary(payload: dict[str, Any]) -> dict[str, Any]:
    status = str(payload.get("status") or "")
    source = payload.get("source") if isinstance(payload.get("source"), dict) else {}
    download_task = payload.get("download_task") if isinstance(payload.get("download_task"), dict) else {}
    media = payload.get("media") if isinstance(payload.get("media"), dict) else {}
    status_labels = {
        "planned": "可生成交接包",
        "review_required": "需要先人工确认",
        "incomplete": "缺少本地媒体文件",
        "not_ready": "任务尚未准备好",
        "not_found": "任务不存在",
    }
    media_label = "已找到本地媒体文件" if media.get("media_exists") else "未找到可交接的本地媒体文件"
    review_label = "已人工标记通过" if download_task.get("accepted") else "尚未人工标记通过"
    if status == "planned" and media.get("media_exists"):
        headline = "VKP 交接包已准备好。"
    elif status == "review_required":
        headline = "任务需要先人工确认后再交给 VKP。"
    elif status == "incomplete":
        headline = "任务没有可交接的本地媒体文件。"
    elif status == "not_ready":
        headline = "任务还不能交给 VKP。"
    else:
        headline = "未生成可用的 VKP 交接包。"

    return {
        "headline": headline,
        "status_label": status_labels.get(status, status or "未知"),
        "platform_label": source.get("platform") or "未知平台",
        "title": source.get("page_title"),
        "quality_label": download_task.get("quality_label"),
        "media_label": media_label,
        "review_label": review_label,
        "next_action": payload.get("next_action"),
    }


def _vkp_ingest_suggestion(payload: dict[str, Any]) -> dict[str, Any]:
    media = payload.get("media") if isinstance(payload.get("media"), dict) else {}
    download_task = payload.get("download_task") if isinstance(payload.get("download_task"), dict) else {}
    media_path = media.get("media_path")
    quality_status = str(download_task.get("quality_status") or "")
    status = str(payload.get("status") or "")
    can_suggest = bool(media.get("media_exists") and media_path and quality_status != "fail" and status in {"planned", "review_required"})
    if not can_suggest:
        return {
            "available": False,
            "will_execute": False,
            "reason": "没有可交接媒体文件，或任务质量未通过。VDO 不会调用 VKP。",
        }
    task_id = download_task.get("task_id")
    workspace = str(Path("video-knowledge-runs") / f"vdo-task-{task_id}")
    command = (
        'video-knowledge '
        f'openclaw-video-ingest "{media_path}" --workspace "{workspace}"'
    )
    return {
        "available": True,
        "will_execute": False,
        "requires_human_review": not bool(download_task.get("accepted")),
        "command": command,
        "workspace": workspace,
        "note": "这是给人工或 VKP 侧使用的命令建议；VDO 不会自动执行。",
    }


def _write_handoff_manifest(payload: dict[str, Any], output_dir: Path, task_id: int) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"task-{task_id}-vkp-handoff.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path
