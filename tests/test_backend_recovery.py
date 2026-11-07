import json

from typer.testing import CliRunner

import video_orchestrator.cli as cli_module
from video_orchestrator.cli import app
from video_orchestrator.runtime.backend_recovery import backend_fallback_decision
from video_orchestrator.runtime.subprocess_runner import CommandResult


runner = CliRunner()


def test_douyin_fresh_cookie_failure_stops_backend_loop():
    decision = backend_fallback_decision(
        url="https://www.douyin.com/video/1234567890",
        error_info={
            "error_type": "browser_session_required",
            "next_action": "请使用 browser-sniffer 捕获。",
        },
        remaining_backends=["yt-dlp", "lux"],
    )

    assert decision["continue"] is False
    assert decision["status"] == "needs_browser_sniffing"
    assert decision["site_adapter"] == "douyin"
    assert decision["skipped_backends"] == ["yt-dlp", "lux"]


def test_dpapi_failure_does_not_retry_another_cookie_reader():
    decision = backend_fallback_decision(
        url="https://www.douyin.com/video/1234567890",
        error_info={"error_type": "browser_cookie_decryption_unavailable"},
        remaining_backends=["yt-dlp"],
    )

    assert decision["continue"] is False
    assert decision["reason"] == "browser_session_state_unavailable"


def test_regular_extractor_failure_keeps_normal_fallbacks():
    decision = backend_fallback_decision(
        url="https://example.com/watch/123",
        error_info={},
        remaining_backends=["yt-dlp"],
    )

    assert decision["continue"] is True
    assert decision["status"] == "fallback_available"


def test_douyin_cli_stops_after_first_fresh_cookie_failure(monkeypatch, tmp_path):
    calls: list[list[str]] = []

    def fake_run_command(args, **kwargs):
        calls.append(list(args))
        return CommandResult(args=args, returncode=1, stdout="", stderr="ERROR: Fresh cookies are needed")

    monkeypatch.setattr(cli_module, "run_command", fake_run_command)
    result = runner.invoke(
        app,
        [
            "download",
            "https://www.douyin.com/video/1234567890",
            "--output-dir",
            str(tmp_path / "downloads"),
            "--db-path",
            str(tmp_path / "tasks.sqlite"),
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert len(calls) == 1
    assert payload["attempts"][0]["fallback_decision"]["skipped_backends"] == ["yt-dlp", "lux"]
    assert payload["recovery"]["fallback_stopped"] is True
