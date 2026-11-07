from pathlib import Path

from video_orchestrator.runtime.operator import operator_review_summary
from video_orchestrator.runtime.quality import build_quality_review
from video_orchestrator.runtime.quality import classify_backend_error


def test_classifies_chrome_cookie_database_error():
    error = classify_backend_error("ERROR: Could not copy Chrome cookie database.")

    assert error is not None
    assert error["error_type"] == "chrome_cookie_database_locked"
    assert "完全退出 Chrome" in error["next_action"]


def test_classifies_fresh_cookie_requirement_as_browser_sniffing_recovery():
    error = classify_backend_error("ERROR: Fresh cookies are needed")

    assert error is not None
    assert error["error_type"] == "browser_session_required"
    assert "browser-sniffer" in error["next_action"]
    assert "不需要导出 Cookie" in error["next_action"]


def test_review_includes_classified_backend_attempt_errors():
    review = build_quality_review(
        status="failed",
        output_files=[],
        attempts=[
            {
                "backend": "yt-dlp-api",
                "returncode": 1,
                **classify_backend_error("ERROR: Could not copy Chrome cookie database."),
            }
        ],
    )

    check = next(item for item in review["checks"] if item["name"] == "backend_attempt_errors")
    assert check["status"] == "warning"
    assert check["errors"][0]["error_type"] == "chrome_cookie_database_locked"


def test_review_warns_when_sniffed_media_looks_like_preview():
    review = build_quality_review(
        status="ok",
        output_files=[],
        sniffed_media={
            "url": "https://cdn.example.com/video/preview.mp4",
            "filename": "preview.mp4",
            "content_length": 500_000,
            "completeness": {
                "risk": "suspected_preview",
                "label": "疑似试看/片段",
                "risk_reasons": ["URL 或文件名包含试看/预览关键词"],
                "next_action": "疑似只捕获到试看或分片。请在已登录/已授权页面播放完整视频后重新嗅探。",
            },
        },
        ffprobe_results=[
            {
                "path": "preview.mp4",
                "returncode": 0,
                "metadata": {"format": {"duration": "30.0", "size": "500000"}},
            }
        ],
    )

    check = next(item for item in review["checks"] if item["name"] == "media_completeness")
    assert check["status"] == "warning"
    assert check["quality_label"] == "疑似试看/片段"
    assert "重新嗅探" in check["next_action"]


def test_operator_quality_labels_backend_attempt_errors(tmp_path: Path):
    review_path = tmp_path / "review-checklist.json"
    review_path.write_text(
        """{
  "checks": [
    {
      "name": "backend_attempt_errors",
      "status": "warning",
      "errors": [
        {
          "backend": "yt-dlp-api",
          "error_type": "chrome_cookie_database_locked",
          "error_label": "Chrome Cookie 数据库无法复制",
          "next_action": "请完全退出 Chrome 后重试"
        }
      ]
    }
  ]
}""",
        encoding="utf-8",
    )

    summary = operator_review_summary(
        {"id": 1, "status": "failed", "backend": "yt-dlp-api"},
        {"review_checklist": str(review_path)},
    )

    assert "Chrome Cookie 数据库无法复制" in summary["reason_labels"]


def test_operator_quality_labels_media_completeness_warning(tmp_path: Path):
    review_path = tmp_path / "review-checklist.json"
    review_path.write_text(
        """{
  "checks": [
    {
      "name": "media_completeness",
      "status": "warning",
      "quality_label": "疑似试看/片段",
      "risk_reasons": ["ffprobe 显示媒体时长少于 60 秒"]
    }
  ]
}""",
        encoding="utf-8",
    )

    summary = operator_review_summary(
        {"id": 1, "status": "finished", "backend": "curl"},
        {"review_checklist": str(review_path)},
    )

    assert summary["quality_status"] == "warn"
    assert "疑似试看/片段" in summary["reason_labels"]
