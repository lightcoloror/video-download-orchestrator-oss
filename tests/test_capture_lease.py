from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

import video_orchestrator.api as api_module
from video_orchestrator.api import app
from video_orchestrator.runtime.capture_lease import capture_lease_summary
from video_orchestrator.runtime.capture_lease import expire_capture_secrets
from video_orchestrator.runtime.capture_lease import new_capture_lease
from video_orchestrator.runtime.sniff_store import redact_sniff_record


client = TestClient(app)


def setup_function():
    api_module.SNIFFED_MEDIA_CAPTURES.clear()
    api_module.SNIFFED_MEDIA_REJECTIONS.clear()


def test_capture_lease_expires_and_destroys_replay_secrets():
    now = datetime(2026, 8, 20, 8, 0, tzinfo=UTC)
    lease = new_capture_lease(captured_at=now, ttl_seconds=60)
    capture = {
        "captured_at": lease["captured_at"],
        "expires_at": lease["expires_at"],
        "sensitive_data_available": True,
        "url": "https://cdn.example.test/video.mp4?x-signature=secret&x-expires=123",
        "request_headers": {"Cookie": "session=secret", "Referer": "https://example.test/watch"},
        "response_headers": {"Content-Type": "video/mp4"},
        "manifest": {
            "source": "browser_sniffer",
            "url": "https://cdn.example.test/video.mp4?x-signature=secret",
            "command": ["curl", "https://cdn.example.test/video.mp4?x-signature=secret"],
        },
    }

    assert capture_lease_summary(capture, now=now)["active"] is True
    assert expire_capture_secrets(capture, now=now + timedelta(seconds=61)) is True
    assert capture["url"] == "https://cdn.example.test/video.mp4"
    assert capture["request_headers"]["Cookie"] == "<redacted>"
    assert "secret" not in str(capture)
    assert capture_lease_summary(capture, now=now + timedelta(seconds=61))["status"] == "expired"


def test_sniffed_signed_media_plan_persists_only_non_replayable_artifact(tmp_path):
    response = client.post(
        "/sniffed-media/plan",
        json={
            "url": "https://cdn.example.test/video.mp4?x-signature=secret-value&x-expires=1780000000",
            "page_url": "https://www.douyin.com/video/1234567890",
            "category": "files",
            "content_type": "video/mp4",
            "request_headers": {
                "Cookie": "session=secret-value",
                "Authorization": "Bearer secret-value",
                "User-Agent": "fixture-agent",
                "Referer": "https://www.douyin.com/video/1234567890?share_nonce=secret-value",
            },
            "response_headers": {"content-type": "video/mp4"},
            "output_dir": str(tmp_path / "downloads"),
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["will_download"] is False
    assert payload["capture_lease"]["status"] == "active"
    artifact = Path(payload["manifest_path"]).read_text(encoding="utf-8")
    assert "secret-value" not in artifact
    assert "x-signature" not in artifact
    assert "x-expires" not in artifact
    assert "share_nonce" not in artifact


def test_expired_capture_is_not_returned_for_execution_even_with_sensitive_flag():
    response = client.post(
        "/sniffed-media/plan",
        json={
            "url": "https://cdn.example.test/video.mp4?token=secret",
            "page_url": "https://www.douyin.com/video/1234567890",
            "category": "files",
            "content_type": "video/mp4",
            "request_headers": {"Cookie": "session=secret"},
            "write_manifest": False,
        },
    )
    assert response.status_code == 200
    capture = api_module.SNIFFED_MEDIA_CAPTURES[0]
    capture["expires_at"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()

    latest = client.get(
        "/sniffed-media/latest",
        params={"page_url": "https://www.douyin.com/video/1234567890", "include_sensitive": True},
    ).json()

    assert latest["status"] == "not_found"
    assert latest["candidates"][0]["capture_lease"]["status"] == "expired"
    assert "secret" not in str(latest)


def test_persisted_capture_dedupe_key_drops_entire_media_query():
    media_url = "https://cdn.example.test/video.mp4?unknown_signature=secret-value&expires=1780000000"
    record = {
        "capture_id": "fixture-capture",
        "url": media_url,
        "dedupe_key": "https://www.douyin.com/video/123?share=secret-value|" + media_url + "|files|files",
    }

    redacted = redact_sniff_record(record)

    assert redacted["url"] == "https://cdn.example.test/video.mp4"
    assert redacted["dedupe_key"].startswith(
        "https://www.douyin.com/video/123|https://cdn.example.test/video.mp4|"
    )
    assert "secret-value" not in str(redacted)
    assert "unknown_signature" not in str(redacted)
