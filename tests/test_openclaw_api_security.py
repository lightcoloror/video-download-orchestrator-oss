from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from video_orchestrator.api import app


client = TestClient(app)


def test_api_confirmed_execute_requires_replay_fields(monkeypatch):
    monkeypatch.setenv("VDO_TELEGRAM_ALLOWED_USER_IDS", "42")
    response = client.post(
        "/integrations/openclaw/telegram/execute",
        json={"url": "https://example.com/video.mp4", "actor_id": "42", "confirm": True},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "request_id_required"
    assert payload["will_download"] is False
    assert payload["execute"] is False


def test_api_confirmed_execute_rejects_stale_request(monkeypatch):
    monkeypatch.setenv("VDO_TELEGRAM_ALLOWED_USER_IDS", "42")
    response = client.post(
        "/integrations/openclaw/telegram/execute",
        json={
            "url": "https://example.com/video.mp4",
            "actor_id": "42",
            "confirm": True,
            "request_id": "telegram-stale-api-001",
            "requested_at": (datetime.now(UTC) - timedelta(minutes=6)).isoformat(),
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "stale_request"
    assert payload["will_download"] is False
    assert payload["execute"] is False
