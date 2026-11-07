import json
from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

import video_orchestrator.api as api_module
import video_orchestrator.runtime.openclaw as openclaw_module
from video_orchestrator.api import app
from video_orchestrator.runtime.openclaw_security import OpenClawReplayGuard


client = TestClient(app)


def test_api_duplicate_execute_calls_mock_once(monkeypatch):
    calls = []
    monkeypatch.setenv("VDO_TELEGRAM_ALLOWED_USER_IDS", "42")
    monkeypatch.setattr(openclaw_module, "DEFAULT_REPLAY_GUARD", OpenClawReplayGuard())
    monkeypatch.setattr(openclaw_module, "write_manifest", lambda manifest, output_dir: Path("mock-manifest.json"))
    monkeypatch.setattr(openclaw_module, "TaskStore", lambda path: object())

    def fake_execute_manifest(**kwargs):
        calls.append(kwargs)
        return {"status": "finished"}

    monkeypatch.setattr(api_module, "execute_manifest", fake_execute_manifest)
    request = {
        "url": "https://example.com/video.mp4",
        "actor_id": "42",
        "confirm": True,
        "request_id": "telegram-api-once-001",
        "requested_at": datetime.now(UTC).isoformat(),
    }

    first = client.post("/integrations/openclaw/telegram/execute", json=request).json()
    duplicate = client.post("/integrations/openclaw/telegram/execute", json=request).json()

    assert first["status"] == "finished"
    assert first["will_download"] is True
    assert "telegram-api-once-001" not in json.dumps(first)
    assert duplicate["status"] == "duplicate_request"
    assert duplicate["will_download"] is False
    assert len(calls) == 1
