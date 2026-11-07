import json
from pathlib import Path

from video_orchestrator.runtime.reports import render_review_package_summary
from video_orchestrator.runtime.reports import write_task_report


def test_report_records_backend_attempt_and_fallback(tmp_path: Path):
    report_path = write_task_report(
        tmp_path,
        {
            "url": "https://example.com/video",
            "status": "failed",
            "attempts": [
                {
                    "backend": "gallery-dl",
                    "returncode": 1,
                    "fallback_reason": "unsupported URL",
                },
                {
                    "backend": "yt-dlp",
                    "returncode": 0,
                },
            ],
        },
    )

    content = report_path.read_text(encoding="utf-8")
    assert "gallery-dl" in content
    assert "unsupported URL" in content
    assert "yt-dlp" in content


def test_report_writes_summary_json(tmp_path: Path):
    report_path = write_task_report(
        tmp_path,
        {
            "task_id": "abc123",
            "url": "https://example.com/video",
            "selected_backend": "yt-dlp",
            "status": "ok",
        },
    )
    summary_path = report_path.with_name("summary.json")

    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary["task_id"] == "abc123"
    assert summary["selected_backend"] == "yt-dlp"


def test_report_records_sniffed_media_summary(tmp_path: Path):
    report_path = write_task_report(
        tmp_path,
        {
            "task_id": "cap",
            "url": "https://cdn.example.com/video.mp4",
            "status": "ok",
            "sniffed_media": {
                "media_type": "MP4",
                "category": "files",
                "quality_score": 85,
                "download_candidate_reason": "direct media file",
            },
        },
    )

    content = report_path.read_text(encoding="utf-8")
    summary = json.loads(report_path.with_name("summary.json").read_text(encoding="utf-8"))
    assert "Browser Sniffed Capture" in content
    assert "direct media file" in content
    assert summary["sniffed_media"]["quality_score"] == 85


def test_report_records_split_statuses_and_redacts_sniffed_secrets(tmp_path: Path):
    report_path = write_task_report(
        tmp_path,
        {
            "task_id": "split-status",
            "url": "https://cdn.example.com/video.mp4?x-signature=top-level-secret",
            "status": "finished",
            "quality_status": "warn",
            "transport_status": "warn",
            "media_integrity_status": "pass",
            "sniffed_media": {
                "capture_id": "capture-1",
                "url": "https://cdn.example.com/video.mp4?token=sniffed-secret",
                "request_headers": {"Cookie": "session=header-secret"},
            },
        },
    )

    summary_path = report_path.with_name("summary.json")
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    serialized = report_path.read_text(encoding="utf-8") + summary_path.read_text(encoding="utf-8")
    assert summary["quality_status"] == "warn"
    assert summary["transport_status"] == "warn"
    assert summary["media_integrity_status"] == "pass"
    assert summary["sniffed_media"]["url"] == "https://cdn.example.com/video.mp4"
    assert "top-level-secret" not in serialized
    assert "sniffed-secret" not in serialized
    assert "header-secret" not in serialized


def test_render_review_package_summary_redacts_url():
    content = render_review_package_summary(
        task={"id": 1, "url": "https://example.com/video?token=secret", "backend": "curl", "status": "finished"},
        verdict="pass",
        artifacts={"report": "report.md", "review_checklist": "review-checklist.json"},
        next_action="Mark accepted only after manually reviewing output quality.",
    )

    assert "# Task Review Package" in content
    assert "token=%3Credacted%3E" in content
    assert "secret" not in content
    assert "review-checklist.json" in content


def test_review_package_summary_includes_handoff():
    summary = render_review_package_summary(
        task={"id": 7, "url": "https://example.com/video.mp4", "status": "finished", "backend": "aria2"},
        artifacts={},
        verdict="needs_review",
        next_action="Inspect output",
        handoff={
            "recommended_ui": {"name": "motrix-next"},
            "next_action": "Run the CLI dry-run first.",
            "commands": {"cli_plan": "python -m video_orchestrator.cli download ... --dry-run"},
        },
    )

    assert "Handoff" in summary
    assert "motrix-next" in summary
    assert "--dry-run" in summary
