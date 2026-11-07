from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

import video_orchestrator.api as api
from video_orchestrator.runtime.mediago import build_mediago_capture_plan, mediago_page_candidates

PAGE = "https://example.test/watch?id=1"


def capture(**changes):
    return {"url": "https://cdn.example.test/full.mp4", "page_url": PAGE,
            "captured_at": datetime.now(UTC).isoformat(), "content_length": 10_000_000,
            "backend": "curl", "request_headers": {"Referer": "https://example.test/"}, **changes}


def plan(value):
    return build_mediago_capture_plan(value, page_url=PAGE, started_at=datetime.now(UTC) - timedelta(seconds=5))


def test_public_capture_becomes_native_task_without_execution():
    result = plan(capture())
    assert result["status"] == "ready"
    assert result["will_download"] is False
    assert result["payload"]["startDownload"] is False
    assert result["payload"]["tasks"][0]["type"] == "direct"


@pytest.mark.parametrize("value", [None, capture(page_url="https://example.test/watch?id=2"),
                                  capture(captured_at=(datetime.now(UTC)-timedelta(minutes=30)).isoformat())])
def test_stale_other_page_and_absent_capture_wait(value):
    assert plan(value)["status"] == "waiting"


@pytest.mark.parametrize("changes", [{"url": "https://cdn.example.test/full.mp4?token=SECRET"},
                                     {"request_headers": {"Cookie": "SECRET"}},
                                     {"request_headers": {"Authorization": "SECRET"}},
                                     {"request_headers": {"Referer": "https://example.test/?token=SECRET"}}])
def test_secrets_never_enter_persistent_task_payload(changes):
    result = plan(capture(**changes))
    assert result["status"] == "review_required"
    assert result["payload"]["tasks"] == []
    assert "SECRET" not in str(result)


@pytest.mark.parametrize("changes", [{"url": "https://cdn.example.test/preview.mp4"},
                                     {"url": "https://cdn.example.test/segment.ts", "category": "segment"},
                                     {"content_range": "bytes 1-99/1000"},
                                     {"url": "https://cdn.example.test/full.m3u8", "backend": "n_m3u8dl_re"}])
def test_incomplete_capture_not_auto_imported(changes):
    assert plan(capture(**changes))["status"] == "waiting"


def test_native_direct_and_magnet_not_opened_as_pages():
    assert mediago_page_candidates(["https://example.test/v.mp4", "magnet:?xt=urn:btih:" + "a"*40], []) == []
    assert mediago_page_candidates(["https://www.douyin.com/video/123"], [])[0]["native_task"] is None


def test_api_is_planning_only(monkeypatch):
    monkeypatch.setattr(api, "_latest_capture_for_page", lambda *args: (capture(), "exact"))
    monkeypatch.setattr(api, "import_to_mediago", lambda *args, **kwargs: pytest.fail("must not import"))
    monkeypatch.setattr(api, "execute_manifest", lambda *args, **kwargs: pytest.fail("must not download"))
    result = TestClient(api.app).post("/external-uis/mediago/page/plan", json={
        "page_url": PAGE, "started_at": (datetime.now(UTC)-timedelta(seconds=5)).isoformat()})
    assert result.status_code == 200
    assert result.json()["status"] == "ready"
    assert result.json()["will_download"] is False


def test_api_rejects_unscoped_time():
    result = TestClient(api.app).post("/external-uis/mediago/page/plan", json={"page_url": PAGE, "started_at": "2026-01-01T00:00:00"})
    assert result.status_code == 422
