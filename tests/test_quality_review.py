from pathlib import Path

from video_orchestrator.runtime.quality import build_quality_review
from video_orchestrator.runtime.quality import discover_output_files
from video_orchestrator.runtime.quality import parse_backend_stdout_json
from video_orchestrator.runtime.quality import write_quality_review


def test_quality_review_marks_success_as_needing_review_when_no_outputs_known():
    review = build_quality_review(status="ok", output_files=[])

    assert review["needs_review"] is True
    assert "no output files were detected" in review["checks"][0]["message"]


def test_quality_review_records_existing_output_file(tmp_path: Path):
    output = tmp_path / "video.mp4"
    output.write_bytes(b"video")

    review = build_quality_review(status="ok", output_files=[output])

    assert review["needs_review"] is True
    assert review["checks"][0]["status"] == "ok"
    assert review["checks"][0]["files"][0]["size"] == 5
    assert review["quality_status"] == "warn"


def test_write_quality_review_creates_json(tmp_path: Path):
    path = write_quality_review(tmp_path, build_quality_review(status="failed", output_files=[]))

    assert path.name == "review-checklist.json"
    assert path.exists()


def test_quality_review_records_backend_stdout_json_and_size_match(tmp_path: Path):
    output = tmp_path / "video.mp4"
    output.write_bytes(b"video")
    backend_result = {"output_file": str(output), "bytes": 5, "size_matches_page": True}

    review = build_quality_review(status="ok", output_files=[output], backend_result=backend_result)

    check = next(item for item in review["checks"] if item["name"] == "backend_stdout_json")
    assert check["status"] == "ok"
    assert check["size_matches_page"] is True
    assert review["backend_result"]["output_file"] == str(output)


def test_quality_review_records_backend_timeout_attempts():
    review = build_quality_review(
        status="failed",
        output_files=[],
        attempts=[
            {
                "backend": "yt-dlp",
                "timed_out": True,
                "timeout_kind": "idle",
                "duration_seconds": 300.1,
                "hard_timeout_seconds": 7200,
                "idle_timeout_seconds": 300,
            }
        ],
    )

    check = next(item for item in review["checks"] if item["name"] == "backend_timeouts")
    assert check["status"] == "warning"
    assert check["timed_out"][0]["timeout_kind"] == "idle"
    assert review["attempts"][0]["backend"] == "yt-dlp"


def test_quality_review_records_sniffed_media_context():
    review = build_quality_review(
        status="ok",
        output_files=[],
        sniffed_media={
            "media_type": "MP4",
            "category": "files",
            "backend": "curl",
            "quality_score": 85,
            "download_candidate_reason": "direct media file",
            "request_headers": {"Referer": "https://example.com/watch"},
        },
    )

    check = next(item for item in review["checks"] if item["name"] == "sniffed_media")
    assert check["status"] == "ok"
    assert check["quality_score"] == 85
    assert review["sniffed_media"]["download_candidate_reason"] == "direct media file"


def test_parse_backend_stdout_json_handles_preceding_text():
    parsed = parse_backend_stdout_json('noise\n{"status":"finished","output_file":"x.mp4"}\n')

    assert parsed == {"status": "finished", "output_file": "x.mp4"}


def test_discover_output_files_prefers_backend_result(tmp_path: Path):
    output = tmp_path / "backend.mp4"
    output.write_bytes(b"video")

    assert discover_output_files(tmp_path, {"output_file": str(output)}) == [output]


def test_quality_review_exposes_failed_completeness_outcome():
    review = build_quality_review(
        status="ok",
        output_files=[],
        sniffed_media={
            "url": "https://cdn.example.com/main.mp4",
            "media_type": "MP4",
            "content_range": "bytes 0-9999999/10000000",
        },
        ffprobe_results=[
            {
                "returncode": 0,
                "metadata": {
                    "format": {"duration": "10", "size": "5000000"},
                    "streams": [{"codec_type": "video"}],
                },
            }
        ],
    )

    assert review["quality_status"] == "fail"
    assert review["quality_label"] == "完整性检查失败"


def test_quality_review_separates_fallback_warning_from_passing_media_integrity(tmp_path: Path):
    output = tmp_path / "video.mp4"
    output.write_bytes(b"\x00\x00\x00\x18ftypisom\x00\x00\x00\x00isommp42")
    review = build_quality_review(
        status="ok",
        output_files=[output],
        fallback_used=True,
        attempts=[
            {
                "backend": "aria2",
                "returncode": 1,
                "error_type": "backend_command_missing",
                "error_label": "下载后端命令不存在",
            },
            {"backend": "python-http", "returncode": 0},
        ],
        sniffed_media={
            "evidence_source": "caller_expected_duration",
            "expected_duration_seconds": 296.008,
            "completeness": {
                "risk": "pending_download_validation",
                "label": "已记录预期时长，待下载后校验",
                "risk_reasons": [],
            },
        },
        ffprobe_results=[
            {
                "path": str(output),
                "returncode": 0,
                "metadata": {
                    "format": {"duration": "296.0", "size": "5"},
                    "streams": [{"codec_type": "video"}, {"codec_type": "audio"}],
                },
            }
        ],
    )

    assert review["transport_status"] == "warn"
    assert review["media_integrity_status"] == "pass"
    assert review["quality_status"] == "warn"


def test_quality_review_marks_short_download_as_media_integrity_failure(tmp_path: Path):
    output = tmp_path / "video.mp4"
    output.write_bytes(b"video")
    review = build_quality_review(
        status="ok",
        output_files=[output],
        sniffed_media={"expected_duration_seconds": 296.008},
        ffprobe_results=[
            {
                "path": str(output),
                "returncode": 0,
                "metadata": {
                    "format": {"duration": "74.0", "size": "5"},
                    "streams": [{"codec_type": "video"}],
                },
            }
        ],
    )

    assert review["transport_status"] == "pass"
    assert review["media_integrity_status"] == "fail"
    assert review["quality_status"] == "fail"


def test_quality_review_redacts_sniffed_url_and_header_secrets(tmp_path: Path):
    review = build_quality_review(
        status="ok",
        output_files=[],
        sniffed_media={
            "capture_id": "capture-1",
            "url": "https://cdn.example.com/video.mp4?x-signature=url-secret&plain=ok",
            "request_headers": {
                "Cookie": "session=header-secret",
                "X-Signature": "header-signature-secret",
            },
        },
    )
    path = write_quality_review(tmp_path, review)
    serialized = path.read_text(encoding="utf-8")

    assert review["sniffed_media"]["url"] == "https://cdn.example.com/video.mp4"
    assert review["sniffed_media"]["request_headers"]["Cookie"] == "<redacted>"
    assert review["sniffed_media"]["request_headers"]["X-Signature"] == "<redacted>"
    assert "url-secret" not in serialized
    assert "header-secret" not in serialized
    assert "header-signature-secret" not in serialized
