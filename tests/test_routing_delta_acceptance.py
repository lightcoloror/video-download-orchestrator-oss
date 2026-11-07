"""Offline acceptance for VDO-ROUTING-DELTA-CLOSE-20260908-01."""
from pathlib import Path
from types import SimpleNamespace
import socket
import subprocess
import threading

import pytest
import requests

from video_orchestrator import external_uis
from video_orchestrator.fallbacks import fallback_plan_for_url
from video_orchestrator.runtime import mediago, quality, subprocess_runner
from video_orchestrator.runtime.backend_recovery import backend_fallback_decision
from video_orchestrator.services import plan_download


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    from video_orchestrator import services
    monkeypatch.setattr(services, "build_ytdlp_api_metadata_preview", lambda: {"status": "mocked"})
    def forbidden(*args, **kwargs):
        raise AssertionError("live network/process/thread action forbidden in this dispatch")
    for module, name in ((socket, "getaddrinfo"), (socket, "create_connection"),
                         (subprocess, "Popen"), (subprocess, "run"), (threading.Thread, "start"),
                         (requests.sessions.Session, "request")):
        monkeypatch.setattr(module, name, forbidden)
    monkeypatch.setattr(mediago, "_resolved_config", lambda: {
        "desktop_http_base": "http://127.0.0.1:9999", "custom_scheme": "mediago", "api_key_env": ""})
    monkeypatch.setattr(subprocess_runner.shutil, "which", lambda command: None)


class Response:
    def __init__(self, body, code=200):
        self.body, self.status_code = body, code
    def json(self):
        return self.body


def test_windows_python_uses_current_interpreter():
    assert subprocess_runner.resolve_command("python") == subprocess_runner.sys.executable


@pytest.mark.parametrize("location", ["scripts", "winget", "user_scripts"])
def test_windows_discovery_uses_bounded_roots(monkeypatch, location):
    monkeypatch.setattr(subprocess_runner, "os", SimpleNamespace(name="nt", environ={
        "LOCALAPPDATA": "D:/synthetic-local", "APPDATA": "D:/synthetic-roaming"}))
    monkeypatch.setattr(subprocess_runner.sysconfig, "get_path", lambda name: "D:/synthetic-python/Scripts")
    version = subprocess_runner.sys.version_info
    paths = {"scripts": "D:/synthetic-python/Scripts/yt-dlp.exe",
             "winget": "D:/synthetic-local/Microsoft/WinGet/Links/yt-dlp.exe",
             "user_scripts": f"D:/synthetic-roaming/Python/Python{version.major}{version.minor}/Scripts/yt-dlp.exe"}
    visited = []
    def is_file(path):
        visited.append(path.as_posix())
        return path.as_posix() == paths[location]
    monkeypatch.setattr(Path, "is_file", is_file)
    assert Path(subprocess_runner.resolve_command("yt-dlp")).as_posix() == paths[location]
    assert len(visited) <= 3


def test_missing_binary_not_reported_available(monkeypatch):
    monkeypatch.setattr(Path, "is_file", lambda path: False)
    assert subprocess_runner.command_exists("fixture-absent") is False


def test_installed_mediago_is_not_online_evidence(monkeypatch):
    monkeypatch.setattr(external_uis, "os", SimpleNamespace(environ={"LOCALAPPDATA": "D:/fixture"}))
    monkeypatch.setattr(Path, "is_file", lambda p: p.as_posix() == "D:/fixture/Programs/mediago/MediaGo.exe")
    spec = external_uis.ExternalUiSpec(name="mediago", purpose="fixture", source_project="fixture",
                                     integration_mode="fixture", routes=[])
    result = external_uis._with_status(spec, check=True)
    assert result.status == "installed_not_probed"
    assert result.installed_path


def test_missing_process_error_is_structured_and_utf8_explicit(monkeypatch):
    observed = {}
    monkeypatch.setattr(subprocess_runner, "resolve_command", lambda command: "D:/fixture/tool.exe")
    def popen(args, **kwargs):
        observed.update(kwargs)
        assert args[0] == "D:/fixture/tool.exe"
        raise FileNotFoundError("fixture missing")
    monkeypatch.setattr(subprocess_runner.subprocess, "Popen", popen)
    result = subprocess_runner.run_command(["fixture", "--version"])
    assert result.returncode == 127
    assert observed["encoding"] == "utf-8" and observed["errors"] == "replace"


@pytest.mark.parametrize("timed_out", [False, True])
def test_doctor_discovery_does_not_imply_successful_version_probe(monkeypatch, timed_out):
    from video_orchestrator import cli
    from video_orchestrator.backends.base import BackendSpec
    monkeypatch.setattr(cli, "command_exists", lambda name: True)
    def run(args, **kwargs):
        assert kwargs == {"hard_timeout_seconds": 5, "idle_timeout_seconds": 3}
        return subprocess_runner.CommandResult(args=args, returncode=1, stdout="", stderr="fixture",
                                               timed_out=timed_out)
    monkeypatch.setattr(cli, "run_command", run)
    result = cli._backend_status(BackendSpec(name="fixture", command="fixture"))
    assert result["discovered"] is True
    assert result["executable_verified"] is False
    assert result["status"] == ("probe_timeout" if timed_out else "probe_failed")


@pytest.mark.parametrize("url", ["https://www.bilibili.com/video/BV1xx411c7mD", "https://www.bilibili.com/video/av123", "https://b23.tv/Fixture"])
def test_bilibili_routes_and_412_stops_retry(url):
    plan = plan_download(url)
    assert plan["classification"] == "bilibili-page"
    assert plan["selected_backend"] == "yt-dlp-api"
    assert plan["selection_source"] == "url_classification"
    decision = backend_fallback_decision(url=url, error_info=quality.classify_backend_error("HTTP Error 412"),
                                         remaining_backends=["yt-dlp", "lux"])
    assert decision["continue"] is False
    assert decision["skipped_backends"] == ["yt-dlp", "lux"]


def test_explicit_backend_survives_automatic_classification():
    plan = plan_download("https://example.com/video.mp4", backend="python-http")
    assert plan["selected_backend"] == "python-http" and plan["selection_source"] == "explicit"


@pytest.mark.parametrize("url", ["https://twitch.tv.evil.test/video", "https://evil-spotify.com/track/1", "https://tumblr.com.evil.test/post/1"])
def test_platform_hostname_requires_label_boundary(url):
    from video_orchestrator.router import classify_url
    assert classify_url(url).kind == "general"


@pytest.mark.parametrize("url", ["magnet:?xt=urn:btih:fixture", "https://example.com/file.torrent"])
def test_p2p_never_falls_back_to_http_download_of_torrent(url):
    assert fallback_plan_for_url(url)["fallback_backends"] == []


@pytest.mark.parametrize("queue", [{"data": {"list": []}}, {"data": {}}, {"data": "html"}])
def test_mediago_health_checks_queue_contract(queue):
    calls = []
    def get(url, **kwargs):
        calls.append(url)
        if url.endswith("/healthy"):
            return Response({})
        if url.endswith("/api/config"):
            return Response({"data": {"local": "D:/fixture-output"}})
        assert kwargs["params"]["pageSize"] == 1
        return Response(queue)
    result = mediago.mediago_status(request_get=get)
    assert result["service_online"] is True
    assert result["api_ready"] == (queue == {"data": {"list": []}})
    assert result["download_verified"] is False
    assert len(calls) == 3


def test_mediago_timeout_is_not_healthy_or_sensitive_error():
    def get(*args, **kwargs):
        raise requests.Timeout("credential must not leak")
    result = mediago.mediago_status(request_get=get)
    assert result["service_online"] is False and result["status"] == "unavailable"
    assert "credential" not in str(result)


def test_api_health_only_claims_liveness():
    from video_orchestrator.api import health
    result = health()
    assert result["status"] == "ok" and result["health_scope"] == "liveness"
    assert result["backend_readiness"] == result["output_validity"] == "not_checked"


WAV = b"RIFF" + (64).to_bytes(4, "little") + b"WAVEfmt " + bytes(64)


@pytest.mark.parametrize("name,data,reason", [
    ("video.mp4", b"<!doctype html><html>login</html>", "text_or_error_response_not_media"),
    ("video.mp4", b'{"error":"denied"}', "text_or_error_response_not_media"),
    ("video.mp4", WAV, "extension_signature_mismatch"),
    ("video.mp4", b"%PDF-1.7\n" + bytes(64), "non_media_signature"),
    ("audio.wav", b"", "output_missing_or_empty"),
])
def test_successful_transport_cannot_hide_invalid_output(tmp_path, name, data, reason):
    output = tmp_path / name
    output.write_bytes(data)
    review = quality.build_quality_review(status="ok", output_files=[output])
    check = next(c for c in review["checks"] if c["name"] == "output_type")
    assert check["files"][0]["reason"] == reason
    assert review["transport_status"] == "pass"
    assert review["media_integrity_status"] == "fail"


def test_signature_success_without_decoding_still_unverified(tmp_path):
    output = tmp_path / "audio.wav"
    output.write_bytes(WAV)
    review = quality.build_quality_review(status="ok", output_files=[output])
    check = next(c for c in review["checks"] if c["name"] == "output_type")
    assert check["status"] == "ok"
    assert review["media_integrity_status"] == "warn"


def test_wrong_mime_with_valid_magic_is_rejected(tmp_path):
    output = tmp_path / "audio.wav"
    output.write_bytes(WAV)
    check = quality._output_type_check([output], {"output_file": str(output), "content_type": "text/html"})
    assert check["status"] == "failed"


def test_missing_signature_library_does_not_claim_validity(tmp_path, monkeypatch):
    output = tmp_path / "audio.wav"
    output.write_bytes(WAV)
    monkeypatch.setattr(quality, "filetype", None)
    assert quality._output_type_check([output], None)["status"] == "warning"


def test_missing_output_is_failed_and_unreadable_is_structured(tmp_path, monkeypatch):
    assert quality._output_type_check([tmp_path / "absent.mp4"], None)["status"] == "failed"
    monkeypatch.setattr(Path, "is_file", lambda path: (_ for _ in ()).throw(PermissionError()))
    result = quality._output_type_check([tmp_path / "blocked.mp4"], None)
    assert result["files"][0]["reason"] == "output_unreadable_or_malformed"
    review = quality.build_quality_review(status="ok", output_files=[tmp_path / "blocked.mp4"])
    assert review["media_integrity_status"] == "fail"


def test_declared_output_list_prevents_unrelated_directory_fallback(tmp_path):
    declared = [str(tmp_path / "missing.mp4"), str(tmp_path / "error.html")]
    (tmp_path / "unrelated.wav").write_bytes(WAV)
    files = quality.discover_output_files(tmp_path, {"output_files": declared})
    assert files == [Path(path) for path in declared]
