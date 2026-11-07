from __future__ import annotations

import json
from typing import Any

from video_orchestrator.runtime.wechat_channels_providers import resolve_public_profile
from video_orchestrator.runtime.wechat_channels_resolve import resolve_wechat_channels_share_auto


SHARE_URL = "https://weixin.qq.com/sph/Fixture123"
PROVIDER_CONFIG = {
    "providers": {
        "public-profile": {
            "enabled": True,
            "endpoint": "https://profile.example.com/api/video/profile",
            "allowed_host_suffixes": ["example.com"],
            "timeout_seconds": 5,
            "max_response_bytes": 2048,
            "transport_order": ["direct", "proxy"],
        }
    }
}


class FakeResponse:
    def __init__(self, payload: dict[str, Any], *, status_code: int = 200, content: bytes | None = None):
        self._payload = payload
        self.status_code = status_code
        self.content = content if content is not None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.headers = {"Content-Length": str(len(self.content))}

    def json(self):
        return self._payload


class FakeStreamingResponse(FakeResponse):
    def __init__(self, chunks: list[bytes], *, status_code: int = 200, content_length: int | None = None):
        super().__init__({}, status_code=status_code, content=b"")
        self._chunks = chunks
        self.headers = {"Content-Length": str(content_length)} if content_length is not None else {}
        self.closed = False

    def iter_content(self, chunk_size: int):
        assert chunk_size == 64 * 1024
        yield from self._chunks

    def close(self):
        self.closed = True


def _profile_payload() -> dict[str, Any]:
    return {
        "data": {
            "data": {
                "feedInfo": {
                    "description": "合成视频号标题",
                    "originVideoUrl": "https://finder.video.qq.com/original.mp4?token=SECRET_ORIGINAL",
                    "h265VideoInfo": {"videoUrl": "https://finder.video.qq.com/h265.mp4?token=SECRET_H265"},
                    "h264VideoInfo": {"videoUrl": "https://finder.video.qq.com/h264.mp4?token=SECRET_H264"},
                    "videoUrl": "https://finder.video.qq.com/default.mp4?token=SECRET_DEFAULT",
                }
            }
        }
    }


def _configure(monkeypatch) -> None:
    monkeypatch.setattr(
        "video_orchestrator.runtime.wechat_channels_providers.wechat_channels_config",
        lambda: PROVIDER_CONFIG,
    )
    monkeypatch.setattr(
        "video_orchestrator.runtime.wechat_channels_providers.site_probe_config",
        lambda: {"proxy_url": "http://127.0.0.1:10808"},
    )


def test_public_profile_selects_original_by_default_without_proxy(monkeypatch):
    _configure(monkeypatch)
    observed: dict[str, Any] = {}

    def fake_get(url, **kwargs):
        observed.update(kwargs)
        return FakeResponse(_profile_payload())

    result = resolve_public_profile(SHARE_URL, request_get=fake_get)

    assert result["status"] == "resolved"
    assert result["quality"] == "original"
    assert result["candidate_count"] == 4
    assert result["media_url"].endswith("token=SECRET_ORIGINAL")
    assert observed["params"] == {"url": SHARE_URL}
    assert observed["stream"] is True
    assert "proxies" not in observed
    assert "Cookie" not in observed["headers"]
    assert "Authorization" not in observed["headers"]


def test_public_profile_compatible_mode_prefers_h264(monkeypatch):
    _configure(monkeypatch)

    result = resolve_public_profile(
        SHARE_URL,
        quality="compatible",
        request_get=lambda *args, **kwargs: FakeResponse(_profile_payload()),
    )

    assert result["status"] == "resolved"
    assert result["quality"] == "h264"
    assert "SECRET_H264" in result["media_url"]


def test_public_profile_retries_proxy_only_after_direct_transport_failure(monkeypatch):
    _configure(monkeypatch)
    attempts: list[dict[str, Any]] = []

    def fake_get(url, **kwargs):
        attempts.append(kwargs)
        if "proxies" not in kwargs:
            raise OSError("direct TLS failure")
        return FakeResponse(_profile_payload())

    result = resolve_public_profile(SHARE_URL, request_get=fake_get)

    assert result["status"] == "resolved"
    assert result["transport"] == "proxy"
    assert len(attempts) == 2
    assert attempts[1]["proxies"]["https"] == "http://127.0.0.1:10808"


def test_public_profile_rejects_oversized_response(monkeypatch):
    _configure(monkeypatch)

    result = resolve_public_profile(
        SHARE_URL,
        transport="direct",
        request_get=lambda *args, **kwargs: FakeResponse(_profile_payload(), content=b"x" * 4096),
    )

    assert result["status"] == "unavailable"
    assert result["reason"] == "provider_unavailable"


def test_public_profile_stream_limit_stops_before_parsing(monkeypatch):
    _configure(monkeypatch)
    response = FakeStreamingResponse([b"x" * 1500, b"y" * 700])

    result = resolve_public_profile(
        SHARE_URL,
        transport="direct",
        request_get=lambda *args, **kwargs: response,
    )

    assert result["status"] == "unavailable"
    assert response.closed is True


def test_public_profile_does_not_retry_proxy_after_http_error(monkeypatch):
    _configure(monkeypatch)
    attempts: list[dict[str, Any]] = []

    def fake_get(url, **kwargs):
        attempts.append(kwargs)
        return FakeResponse({}, status_code=503)

    result = resolve_public_profile(SHARE_URL, request_get=fake_get)

    assert result["status"] == "unavailable"
    assert len(attempts) == 1
    assert "proxies" not in attempts[0]


def test_public_profile_does_not_retry_proxy_after_invalid_payload(monkeypatch):
    _configure(monkeypatch)
    attempts: list[dict[str, Any]] = []

    def fake_get(url, **kwargs):
        attempts.append(kwargs)
        return FakeResponse({"unexpected": True})

    result = resolve_public_profile(SHARE_URL, request_get=fake_get)

    assert result["status"] == "unavailable"
    assert len(attempts) == 1


def test_auto_resolver_falls_back_from_unconfigured_helper_to_public_provider(monkeypatch):
    monkeypatch.setattr(
        "video_orchestrator.runtime.wechat_channels_resolve.resolve_wechat_channels_share",
        lambda *args, **kwargs: {"status": "not_configured", "reason": "helper_missing"},
    )
    monkeypatch.setattr(
        "video_orchestrator.runtime.wechat_channels_resolve.resolve_public_profile",
        lambda *args, **kwargs: {
            "status": "resolved",
            "provider": "public-profile",
            "transport": "direct",
            "title": "标题",
            "source": "fixture",
            "quality": "original",
            "quality_mode": "highest",
            "media_url": "https://finder.video.qq.com/video.mp4?token=SECRET",
            "candidate_count": 1,
            "available_candidates": ["original"],
        },
    )
    monkeypatch.setattr(
        "video_orchestrator.runtime.wechat_channels_resolve.validate_public_http_url",
        lambda *args, **kwargs: None,
    )

    result = resolve_wechat_channels_share_auto(SHARE_URL)

    assert result["status"] == "resolved"
    assert result["provider"] == "public-profile"
    assert [item["provider"] for item in result["provider_attempts"]] == ["local-helper", "public-profile"]
    assert "SECRET" not in result["redacted_media_url"]
