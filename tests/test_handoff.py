from video_orchestrator.runtime.handoff import build_task_handoff
from video_orchestrator.runtime.handoff import build_url_handoff
from video_orchestrator.runtime.task_store import TaskStore


def test_handoff_for_general_url_prefers_metube_or_cli_plan():
    payload = build_url_handoff("https://example.com/watch")

    assert payload["will_download"] is False
    assert payload["route"]["preferred_backend"] == "yt-dlp-api"
    assert payload["recommended_ui"]["name"] == "metube"
    assert "python -m video_orchestrator.cli download" in payload["commands"]["cli_plan"]


def test_handoff_for_hls_manifest_prefers_browser_or_manifest_tools():
    payload = build_url_handoff("https://cdn.example.com/live/master.m3u8")

    assert payload["will_download"] is False
    assert payload["route"]["kind"] == "manifest"
    assert payload["recommended_ui"]["name"] in {"hls-downloader", "metube"}
    assert payload["safety"]["requires_explicit_execution"] is True


def test_handoff_for_direct_file_prefers_aria2_ui():
    payload = build_url_handoff("https://cdn.example.com/video.mp4")

    assert payload["route"]["preferred_backend"] == "aria2"
    assert payload["recommended_ui"]["name"] == "motrix-next"


def test_task_handoff_for_retryable_task_includes_resume_command(tmp_path):
    db_path = tmp_path / "tasks.sqlite"
    store = TaskStore(db_path)
    task_id = store.create_task("https://example.com/watch", "browser-sniffer")
    store.mark_retryable(task_id)

    payload = build_task_handoff(task_id, db_path=db_path)

    assert payload["will_download"] is False
    assert payload["task"]["id"] == task_id
    assert "task resume" in payload["commands"]["task_resume_dry_run"]
