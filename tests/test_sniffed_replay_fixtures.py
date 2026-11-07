import json
from pathlib import Path

from fastapi.testclient import TestClient

import video_orchestrator.api as api_module
from video_orchestrator.api import app


client = TestClient(app)
FIXTURE_DIR = Path(__file__).parent / "fixtures" / "sniffed"


def setup_function():
    api_module.SNIFFED_MEDIA_CAPTURES.clear()
    api_module.SNIFFED_MEDIA_REJECTIONS.clear()


def test_sniffed_replay_fixtures_route_and_redact(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for fixture_path in sorted(FIXTURE_DIR.glob("*.json")):
        fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
        payload = dict(fixture["payload"])
        payload["output_dir"] = str(tmp_path / "downloads" / fixture["name"])

        response = client.post("/sniffed-media/plan", json=payload)

        assert response.status_code == 200, fixture["name"]
        body = response.json()
        assert body["manifest"]["selected_backend"] == fixture["expected_backend"]
        assert body["quality_score"] >= fixture["expected_quality_min"]
        assert body["manifest"]["sniffed_media"]["capture_session_id"] == payload["capture_session_id"]
        assert body["manifest"]["sniffed_media"]["page_url"] == payload["page_url"]
        assert "TOKEN_PLACEHOLDER" not in str(body)
        assert "SESSION_PLACEHOLDER" not in str(body)

        latest = client.get(
            "/sniffed-media/latest",
            params={
                "page_url": payload["page_url"],
                "capture_session_id": payload["capture_session_id"],
            },
        ).json()

        assert latest["status"] == "ok"
        assert latest["capture"]["backend"] == fixture["expected_backend"]
        assert latest["capture"]["capture_session_id"] == payload["capture_session_id"]
        assert "TOKEN_PLACEHOLDER" not in str(latest)
        assert "SESSION_PLACEHOLDER" not in str(latest)
