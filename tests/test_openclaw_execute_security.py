from datetime import UTC, datetime, timedelta
from pathlib import Path

import video_orchestrator.runtime.openclaw as openclaw_module
from video_orchestrator.runtime.openclaw import execute_openclaw_telegram_command
from video_orchestrator.runtime.openclaw_security import OpenClawReplayGuard


def _execute(*, request_id, requested_at, guard, execute_func):
    return execute_openclaw_telegram_command(
        "https://example.com/video.mp4",
        actor_id="42",
        allowed_actor_ids=["42"],
        confirm=True,
        request_id=request_id,
        requested_at=requested_at,
        replay_guard=guard,
        execute_manifest_func=execute_func,
    )


def test_confirmed_execute_stops_before_downloader_without_replay_fields():
    calls = []
    payload = _execute(
        request_id=None,
        requested_at=None,
        guard=OpenClawReplayGuard(),
        execute_func=lambda **kwargs: calls.append(kwargs),
    )

    assert payload["status"] == "request_id_required"
    assert payload["will_download"] is False
    assert payload["execute"] is False
    assert calls == []


def test_confirmed_execute_stops_before_downloader_when_stale():
    calls = []
    payload = _execute(
        request_id="telegram-stale-001",
        requested_at=(datetime.now(UTC) - timedelta(minutes=6)).isoformat(),
        guard=OpenClawReplayGuard(),
        execute_func=lambda **kwargs: calls.append(kwargs),
    )

    assert payload["status"] == "stale_request"
    assert payload["will_download"] is False
    assert calls == []


def test_duplicate_confirmed_execute_calls_mock_downloader_once(monkeypatch):
    calls = []
    guard = OpenClawReplayGuard()
    now = datetime.now(UTC).isoformat()

    monkeypatch.setattr(openclaw_module, "write_manifest", lambda manifest, output_dir: Path("mock-manifest.json"))
    monkeypatch.setattr(openclaw_module, "TaskStore", lambda path: object())

    def fake_execute(**kwargs):
        calls.append(kwargs)
        return {"status": "finished"}

    first = _execute(
        request_id="telegram-once-001",
        requested_at=now,
        guard=guard,
        execute_func=fake_execute,
    )
    duplicate = _execute(
        request_id="telegram-once-001",
        requested_at=now,
        guard=guard,
        execute_func=fake_execute,
    )

    assert first["status"] == "finished"
    assert first["will_download"] is True
    assert duplicate["status"] == "duplicate_request"
    assert duplicate["will_download"] is False
    assert len(calls) == 1
