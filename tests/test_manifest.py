import json
from pathlib import Path

import pytest

from video_orchestrator.runtime.manifests import write_manifest
from video_orchestrator.services import plan_download


def test_plan_download_creates_manifest_without_running_backend():
    manifest = plan_download("https://example.com/video")

    assert manifest["url"] == "https://example.com/video"
    assert manifest["selected_backend"] == "yt-dlp-api"
    assert manifest["status"] == "planned"
    assert manifest["command"][:3] == ["python", "-m", "yt_dlp"]
    assert manifest["media_kind"] == "unknown"


def test_plan_download_auto_identifies_direct_audio_without_extracting_video_audio():
    manifest = plan_download("https://cdn.example.com/podcast/episode.m4a")

    assert manifest["classification"] == "direct-transfer"
    assert manifest["media_kind"] == "audio"
    assert manifest["selected_backend"] == "aria2"


def test_plan_download_passes_explicit_ytdlp_ejs_options():
    manifest = plan_download(
        "https://www.youtube.com/watch?v=abc123",
        ytdlp_options={
            "cookies": "fixtures/test-cookies.txt",
            "js_runtimes": "node",
            "remote_components": ["ejs:github"],
        },
    )

    command = manifest["command"]
    assert command[command.index("--cookies") + 1] == "fixtures/test-cookies.txt"
    assert command[command.index("--js-runtimes") + 1] == "node"
    assert command[command.index("--remote-components") + 1] == "ejs:github"
    assert command[-1] == "https://www.youtube.com/watch?v=abc123"
    assert manifest["yt_dlp_options"]["remote_components"] == ["ejs:github"]


def test_write_manifest_creates_json_file(tmp_path: Path):
    manifest = plan_download("https://example.com/video")

    path = write_manifest(manifest, tmp_path)

    assert path.parent == tmp_path / ".vdo" / "manifests"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["url"] == "https://example.com/video"
    assert data["status"] == "planned"


def test_plan_download_records_minimal_expected_duration_evidence():
    manifest = plan_download(
        "https://cdn.example.com/video.mp4",
        expected_duration_seconds=296.008,
    )

    assert manifest["sniffed_media"] == {
        "evidence_source": "caller_expected_duration",
        "expected_duration_seconds": 296.008,
        "completeness": {
            "risk": "pending_download_validation",
            "label": "已记录预期时长，待下载后校验",
            "risk_reasons": [],
            "expected_duration_seconds": 296.008,
            "next_action": "下载后使用 ffprobe 时长与预期时长进行完整性校验。",
        },
    }


def test_plan_download_records_positive_output_estimate_for_disk_preflight():
    manifest = plan_download(
        "https://cdn.example.com/video.mp4",
        estimated_output_bytes=512 * 1024**2,
    )

    assert manifest["estimated_output_bytes"] == 512 * 1024**2


@pytest.mark.parametrize("value", [0, -1, True])
def test_plan_download_rejects_invalid_output_estimate(value):
    with pytest.raises(ValueError, match="estimated_output_bytes"):
        plan_download("https://cdn.example.com/video.mp4", estimated_output_bytes=value)


@pytest.mark.parametrize("value", [0, -1, float("inf"), float("nan")])
def test_plan_download_rejects_invalid_expected_duration(value: float):
    with pytest.raises(ValueError, match="finite number greater than zero"):
        plan_download("https://cdn.example.com/video.mp4", expected_duration_seconds=value)
