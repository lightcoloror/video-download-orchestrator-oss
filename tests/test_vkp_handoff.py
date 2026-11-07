import json
from pathlib import Path

from video_orchestrator.runtime.task_store import TaskStore
from video_orchestrator.runtime.vkp_handoff import build_vkp_handoff


def _write_json(path: Path, payload: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _task(tmp_path: Path, *, status: str = "finished") -> tuple[Path, TaskStore, int, Path]:
    db_path = tmp_path / "tasks.sqlite"
    store = TaskStore(db_path)
    task_id = store.create_task("https://www.youtube.com/watch?v=abc123&si=secret", "yt-dlp-api")
    if status == "accepted":
        store.mark_finished(task_id, 0)
        store.mark_accepted(task_id)
    elif status == "finished":
        store.mark_finished(task_id, 0)
    elif status == "failed":
        store.mark_failed(task_id, 1)
    elif status == "retryable":
        store.mark_retryable(task_id)
    media = tmp_path / "downloads" / "video.mp4"
    media.parent.mkdir(parents=True, exist_ok=True)
    media.write_bytes(b"video")
    return db_path, store, task_id, media


def test_vkp_handoff_finished_pass_with_output_file(tmp_path: Path):
    db_path, store, task_id, media = _task(tmp_path)
    review_path = _write_json(
        tmp_path / "downloads" / ".vdo" / "reports" / str(task_id) / "review-checklist.json",
        {
            "checks": [{"name": "output_files", "status": "ok"}],
            "backend_result": {"output_file": str(media), "size_matches_page": True},
        },
    )
    summary_path = _write_json(
        tmp_path / "downloads" / ".vdo" / "reports" / str(task_id) / "summary.json",
        {"title": "示例视频", "subtitle_paths": [str(tmp_path / "downloads" / "video.zh-CN.vtt")]},
    )
    manifest_path = _write_json(
        tmp_path / "downloads" / ".vdo" / "manifests" / "manifest.json",
        {"url": "https://www.youtube.com/watch?v=abc123&token=secret", "route": {"kind": "general"}, "selected_backend": "yt-dlp-api"},
    )
    store.add_artifact(task_id, "review_checklist", review_path)
    store.add_artifact(task_id, "summary", summary_path)
    store.add_artifact(task_id, "manifest", manifest_path)

    payload = build_vkp_handoff(task_id, db_path=db_path, output_dir=tmp_path / "handoffs")

    assert payload["contract"] == "vdo_vkp_handoff_v1"
    assert payload["will_download"] is False
    assert payload["execute"] is False
    assert payload["status"] == "planned"
    assert payload["source"]["platform"] == "youtube"
    assert "%3Credacted%3E" in payload["source"]["canonical_url"]
    assert payload["source"]["page_title"] == "示例视频"
    assert payload["media"]["media_exists"] is True
    assert payload["media"]["size_bytes"] == 5
    assert payload["sidecars"]["subtitle_paths"]
    assert payload["download_task"]["accepted"] is False
    assert payload["human_summary"]["headline"] == "VKP 交接包已准备好。"
    assert payload["human_summary"]["review_label"] == "尚未人工标记通过"
    assert payload["vkp_ingest_suggestion"]["available"] is True
    assert payload["vkp_ingest_suggestion"]["will_execute"] is False
    assert "openclaw-video-ingest" in payload["vkp_ingest_suggestion"]["command"]
    assert payload["handoff_manifest_path"].endswith("task-1-vkp-handoff.json")


def test_vkp_handoff_finished_without_media_is_incomplete(tmp_path: Path):
    db_path, store, task_id, _media = _task(tmp_path)
    review_path = _write_json(
        tmp_path / "downloads" / ".vdo" / "reports" / str(task_id) / "review-checklist.json",
        {"checks": [{"name": "output_files", "status": "ok"}], "backend_result": {"size_matches_page": True}},
    )
    store.add_artifact(task_id, "review_checklist", review_path)

    payload = build_vkp_handoff(task_id, db_path=db_path, write_manifest=False)

    assert payload["status"] == "incomplete"
    assert payload["media"]["media_exists"] is False
    assert "未找到" in payload["next_action"]
    assert payload["human_summary"]["status_label"] == "缺少本地媒体文件"
    assert payload["vkp_ingest_suggestion"]["available"] is False
    assert "handoff_manifest_path" not in payload


def test_vkp_handoff_failed_task_is_not_ready(tmp_path: Path):
    db_path, _store, task_id, _media = _task(tmp_path, status="failed")

    payload = build_vkp_handoff(task_id, db_path=db_path, write_manifest=False)

    assert payload["status"] == "not_ready"
    assert payload["download_task"]["quality_status"] == "fail"
    assert "任务未完成或下载失败" in payload["download_task"]["reason_labels"]
    assert payload["vkp_ingest_suggestion"]["available"] is False


def test_vkp_handoff_require_accepted_blocks_unaccepted_task(tmp_path: Path):
    db_path, store, task_id, media = _task(tmp_path)
    review_path = _write_json(
        tmp_path / "downloads" / ".vdo" / "reports" / str(task_id) / "review-checklist.json",
        {
            "checks": [{"name": "output_files", "status": "ok"}],
            "backend_result": {"output_file": str(media), "size_matches_page": True},
        },
    )
    store.add_artifact(task_id, "review_checklist", review_path)

    payload = build_vkp_handoff(task_id, db_path=db_path, require_accepted=True, write_manifest=False)

    assert payload["status"] == "review_required"
    assert payload["will_download"] is False
    assert payload["download_task"]["accepted"] is False
    assert payload["human_summary"]["status_label"] == "需要先人工确认"


def test_vkp_handoff_accepted_task_sets_human_reviewed(tmp_path: Path):
    db_path, store, task_id, media = _task(tmp_path, status="accepted")
    review_path = _write_json(
        tmp_path / "downloads" / ".vdo" / "reports" / str(task_id) / "review-checklist.json",
        {
            "checks": [{"name": "output_files", "status": "ok"}],
            "backend_result": {"output_file": str(media), "size_matches_page": True},
        },
    )
    store.add_artifact(task_id, "review_checklist", review_path)

    payload = build_vkp_handoff(task_id, db_path=db_path, require_accepted=True, write_manifest=False)

    assert payload["status"] == "planned"
    assert payload["download_task"]["accepted"] is True
    assert payload["download_task"]["human_reviewed"] is True
    assert payload["vkp_ingest_suggestion"]["requires_human_review"] is False
