import pytest

from video_orchestrator.runtime.quality import quality_outcome, _output_type_check


@pytest.mark.parametrize("checks", [[], [{"name": "unrelated", "status": "ok"}]])
def test_missing_axis_evidence_is_not_a_pass(checks):
    result = quality_outcome({"checks": checks})
    assert result["transport_status"] == "warn"
    assert result["media_integrity_status"] == "warn"


@pytest.mark.parametrize("status", ["skipped", "unknown", None])
def test_inconclusive_axis_evidence_is_not_a_pass(status):
    result = quality_outcome({"checks": [
        {"name": "backend_status", "status": status},
        {"name": "output_files", "status": status},
    ]})
    assert result["transport_status"] == "warn"
    assert result["media_integrity_status"] == "warn"


def test_failure_takes_precedence_over_missing_evidence():
    result = quality_outcome({"checks": [
        {"name": "output_files", "status": "failed"},
        {"name": "ffprobe", "status": "skipped"},
    ]})
    assert result["media_integrity_status"] == "fail"


def test_success_with_optional_skipped_check_remains_initial_pass():
    result = quality_outcome({"checks": [
        {"name": "output_files", "status": "ok"},
        {"name": "expected_extension", "status": "skipped"},
    ]})
    assert result["media_integrity_status"] == "pass"


@pytest.mark.parametrize("payload", [b"<!doctype html><html>Denied", b'{"error": 403}', b'<?xml version="1.0"?>'])
def test_error_document_with_video_extension_fails(tmp_path, payload):
    path = tmp_path / "video.mp4"
    path.write_bytes(payload)
    assert _output_type_check([path], None)["status"] == "failed"


def test_missing_output_cannot_be_hidden_by_valid_output(tmp_path):
    valid = tmp_path / "video.mp4"
    valid.write_bytes(b"\x00\x00\x00\x18ftypisom\x00\x00\x00\x00isommp42")
    assert _output_type_check([valid, tmp_path / "missing.mp4"], None)["status"] == "failed"


def test_signature_does_not_certify_a_misnamed_file(tmp_path):
    path = tmp_path / "video.mp3"
    path.write_bytes(b"\x00\x00\x00\x18ftypisom\x00\x00\x00\x00isommp42")
    assert _output_type_check([path], None)["status"] == "failed"


def test_missing_signature_library_stays_inconclusive(tmp_path, monkeypatch):
    import video_orchestrator.runtime.quality as quality
    monkeypatch.setattr(quality, "filetype", None)
    path = tmp_path / "video.mp4"
    path.write_bytes(b"\x00\x00\x00\x18ftypisom\x00\x00\x00\x00isommp42")
    assert _output_type_check([path], None)["status"] == "warning"
