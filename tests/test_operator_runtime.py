from pathlib import Path

from video_orchestrator.runtime.operator import artifact_map
from video_orchestrator.runtime.operator import operator_review_summary
from video_orchestrator.runtime.operator import read_json_artifact
from video_orchestrator.runtime.operator import review_verdict


def test_artifact_map_groups_paths_by_kind():
    artifacts = [
        {"kind": "report", "path": "downloads/.vdo/reports/1/report.md"},
        {"kind": "review_checklist", "path": "downloads/.vdo/reports/1/review-checklist.json"},
    ]

    assert artifact_map(artifacts) == {
        "report": "downloads/.vdo/reports/1/report.md",
        "review_checklist": "downloads/.vdo/reports/1/review-checklist.json",
    }


def test_read_json_artifact_returns_dict_only(tmp_path: Path):
    dict_path = tmp_path / "review.json"
    list_path = tmp_path / "list.json"
    dict_path.write_text('{"status":"ok"}', encoding="utf-8")
    list_path.write_text("[1, 2]", encoding="utf-8")

    assert read_json_artifact(str(dict_path)) == {"status": "ok"}
    assert read_json_artifact(str(list_path)) == {}
    assert read_json_artifact(str(tmp_path / "missing.json")) == {}
    assert read_json_artifact(None) == {}


def test_review_verdict_passes_finished_ok_review():
    task = {"status": "finished"}
    review = {"checks": [{"name": "output_files", "status": "ok"}], "backend_result": {"size_matches_page": True}}

    assert review_verdict(task, review) == "pass"


def test_review_verdict_warns_on_warning_check():
    task = {"status": "finished"}
    review = {"checks": [{"name": "ffprobe", "status": "warning"}], "backend_result": {"size_matches_page": True}}

    assert review_verdict(task, review) == "warn"


def test_review_verdict_fails_unfinished_or_failed_review():
    assert review_verdict({"status": "retryable"}, {}) == "fail"
    assert review_verdict({"status": "finished"}, {"checks": [{"status": "failed"}]}) == "fail"
    assert review_verdict({"status": "finished"}, {"backend_result": {"size_matches_page": False}}) == "fail"


def test_operator_review_summary_builds_next_action(tmp_path: Path):
    review_path = tmp_path / "review-checklist.json"
    review_path.write_text(
        '{"checks":[{"name":"output_files","status":"ok"}],"backend_result":{"size_matches_page":true}}',
        encoding="utf-8",
    )
    task = {"id": 1, "status": "finished"}
    artifacts = {"review_checklist": str(review_path)}

    summary = operator_review_summary(task, artifacts)

    assert summary["verdict"] == "pass"
    assert summary["quality_status"] == "pass"
    assert summary["quality_label"] == "可标记通过"
    assert summary["reason_labels"] == ["输出文件检查通过"]
    assert summary["recommended_actions"] == ["人工确认后标记通过"]
    assert summary["can_accept"] is True
    assert summary["can_rerun"] is False
    assert summary["can_resume"] is False
    assert summary["review"]["backend_result"]["size_matches_page"] is True
    assert summary["summary"] == {}
    assert "标记通过" in summary["next_action"]


def test_operator_review_summary_warns_on_range_capture(tmp_path: Path):
    review_path = tmp_path / "review-checklist.json"
    review_path.write_text(
        """
        {
          "checks": [
            {"name": "sniffed_media", "status": "warning", "message": "browser-sniffed capture included range response"}
          ],
          "sniffed_media": {"content_range": "bytes 0-99/999"},
          "backend_result": {"size_matches_page": true}
        }
        """,
        encoding="utf-8",
    )
    task = {"id": 1, "status": "finished", "backend": "curl"}

    summary = operator_review_summary(task, {"review_checklist": str(review_path)})

    assert summary["quality_status"] == "warn"
    assert summary["quality_label"] == "需要人工确认"
    assert "捕获到 Range 分片" in summary["reason_labels"]
    assert summary["can_accept"] is True
    assert summary["can_rerun"] is True
    assert summary["can_resume"] is False


def test_operator_review_summary_fails_failed_task():
    summary = operator_review_summary({"id": 1, "status": "failed", "backend": "yt-dlp"}, {})

    assert summary["quality_status"] == "fail"
    assert summary["quality_label"] == "需要恢复"
    assert "任务未完成或下载失败" in summary["reason_labels"]
    assert summary["can_accept"] is False
    assert summary["can_rerun"] is True
    assert summary["can_resume"] is False
    assert "重新执行" in summary["recommended_actions"]


def test_operator_review_summary_retryable_can_resume():
    summary = operator_review_summary({"id": 1, "status": "retryable", "backend": "browser-sniffer"}, {})

    assert summary["quality_status"] == "fail"
    assert "等待页面嗅探恢复" in summary["reason_labels"]
    assert summary["can_accept"] is False
    assert summary["can_rerun"] is False
    assert summary["can_resume"] is True
    assert "页面嗅探恢复" in summary["recommended_actions"]


def test_operator_review_summary_fails_size_mismatch(tmp_path: Path):
    review_path = tmp_path / "review-checklist.json"
    review_path.write_text(
        '{"checks":[{"name":"output_files","status":"ok"}],"backend_result":{"size_matches_page":false}}',
        encoding="utf-8",
    )

    summary = operator_review_summary({"id": 1, "status": "finished", "backend": "curl"}, {"review_checklist": str(review_path)})

    assert summary["quality_status"] == "fail"
    assert "文件大小与页面信息不匹配" in summary["reason_labels"]
    assert summary["can_accept"] is False
    assert summary["can_rerun"] is True
