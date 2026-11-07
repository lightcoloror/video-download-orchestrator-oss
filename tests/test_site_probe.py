from pathlib import Path

import pytest

import video_orchestrator.runtime.site_probe as site_probe
from video_orchestrator.runtime.config import site_probe_config
from video_orchestrator.runtime.site_probe import extract_public_page_metadata
from video_orchestrator.runtime.url_safety import UnsafeUrlError
from video_orchestrator.runtime.site_probe import probe_public_page


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "site_probe"


def test_extracts_bilibili_public_metadata():
    html = (FIXTURE_DIR / "bilibili-public.html").read_text(encoding="utf-8")

    metadata = extract_public_page_metadata(
        html,
        adapter_name="bilibili",
        page_url="https://www.bilibili.com/video/BV1xx411c7mD?token=secret",
    )

    assert metadata["title"] == "示例 B站视频_哔哩哔哩_bilibili"
    assert metadata["bvid"] == "BV1xx411c7mD"
    assert metadata["aid"] == "123456"
    assert metadata["cid"] == "654321"
    assert metadata["has_public_video_signal"] is True


def test_probe_public_page_extracts_xiaohongshu_metadata():
    html = (FIXTURE_DIR / "xiaohongshu-public.html").read_text(encoding="utf-8")

    def fake_fetch(url, headers, timeout_seconds):
        return html, "text/html", "https://www.xiaohongshu.com/explore/note-example?token=secret"

    payload = probe_public_page(
        "https://www.xiaohongshu.com/explore/note-example?token=secret",
        headers={"Cookie": "session=secret", "User-Agent": "test"},
        fetch_text=fake_fetch,
    )

    probe = payload["probe"]
    assert payload["will_download"] is False
    assert payload["site_adapter"]["name"] == "xiaohongshu"
    assert probe["probe_status"] == "ok"
    assert probe["title"] == "示例小红书笔记 - 小红书"
    assert probe["video_id"] == "note-example"
    assert probe["request_headers"]["Cookie"] == "<redacted>"
    assert "secret" not in str(payload)


def test_probe_public_page_extracts_douyin_metadata():
    html = (FIXTURE_DIR / "douyin-public.html").read_text(encoding="utf-8")

    def fake_fetch(url, headers, timeout_seconds):
        return html, "text/html", url

    payload = probe_public_page("https://www.douyin.com/video/1234567890", fetch_text=fake_fetch)

    assert payload["site_adapter"]["name"] == "douyin"
    assert payload["probe"]["probe_status"] == "ok"
    assert payload["probe"]["video_id"] == "aweme-example"


def test_probe_public_page_without_signals_needs_browser_sniffing():
    def fake_fetch(url, headers, timeout_seconds):
        return "<html><body>no public metadata</body></html>", "text/html", url

    payload = probe_public_page("https://xhslink.com/example", fetch_text=fake_fetch)

    assert payload["probe"]["probe_status"] == "needs_browser_sniffing"
    assert "页面嗅探" in payload["probe"]["next_action"]


def test_probe_public_page_unsupported_url_is_planning_only():
    payload = probe_public_page("https://example.com/watch?token=secret")

    assert payload["will_download"] is False
    assert payload["site_adapter"]["matched"] is False
    assert payload["probe"]["probe_status"] == "unsupported"
    assert payload["url"] == "https://example.com/watch?token=%3Credacted%3E"
    assert "secret" not in str(payload)


def test_probe_public_page_returns_blocked_for_unsafe_default_fetch(monkeypatch):
    def reject_unsafe_url(*_args, **_kwargs):
        raise UnsafeUrlError("non_public_address")

    monkeypatch.setattr(site_probe, "_requests_fetch_text", reject_unsafe_url)
    payload = site_probe.probe_public_page("https://www.bilibili.com/video/BV1xx411c7mD")

    assert payload["will_download"] is False
    assert payload["probe"]["probe_status"] == "blocked"
    assert payload["probe"]["block_reason"] == "non_public_address"
    assert "公开页面地址不安全" in payload["probe"]["next_action"]

class _FakeResponse:
    def __init__(self, *, content_type="text/html", chunks=None, location=None):
        self.headers = {"content-type": content_type}
        if location:
            self.headers["location"] = location
        self.is_redirect = location is not None
        self.encoding = "utf-8"
        self._chunks = chunks or [b"<title>fixture</title>"]
        self.closed = False

    def raise_for_status(self):
        return None

    def iter_content(self, chunk_size):
        yield from self._chunks

    def close(self):
        self.closed = True


class _FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.trust_env = True
        self.proxies = {"https": "http://environment.invalid:9999"}

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.pop(0)


def _probe_settings(**overrides):
    settings = {
        "proxy_url": site_probe_config()["proxy_url"],
        "timeout_seconds": 10,
        "max_redirects": 3,
        "max_response_bytes": 1024,
    }
    settings.update(overrides)
    return settings


def test_secure_probe_uses_only_configured_proxy_and_safe_headers():
    session = _FakeSession([_FakeResponse()])

    text, content_type, final_url, security = site_probe._requests_fetch_text(
        "https://www.bilibili.com/video/BV1",
        {"User-Agent": "fixture", "Cookie": "secret", "Host": "internal"},
        30,
        allowed_host_suffixes=("bilibili.com", "b23.tv"),
        settings=_probe_settings(),
        session=session,
    )

    assert text == "<title>fixture</title>"
    assert content_type == "text/html"
    assert final_url == "https://www.bilibili.com/video/BV1"
    assert session.trust_env is False
    assert session.proxies == {"http": site_probe_config()["proxy_url"], "https": site_probe_config()["proxy_url"]}
    assert session.calls[0][1]["headers"] == {"User-Agent": "fixture"}
    assert security["dropped_header_names"] == ["Cookie", "Host"]
    assert security["security_mode"] == "trusted_local_proxy_allowlist"


def test_secure_probe_blocks_cross_domain_redirect_before_second_request():
    session = _FakeSession([_FakeResponse(location="https://evil.example/private")])

    with pytest.raises(UnsafeUrlError, match="host_not_allowed"):
        site_probe._requests_fetch_text(
            "https://b23.tv/example",
            {},
            10,
            allowed_host_suffixes=("bilibili.com", "b23.tv"),
            settings=_probe_settings(),
            session=session,
        )

    assert len(session.calls) == 1



def test_secure_probe_blocks_large_or_media_response():
    large = _FakeSession([_FakeResponse(chunks=[b"a" * 700, b"b" * 700])])
    with pytest.raises(UnsafeUrlError, match="response_too_large") as large_error:
        site_probe._requests_fetch_text(
            "https://www.bilibili.com/video/BV1",
            {},
            10,
            allowed_host_suffixes=("bilibili.com",),
            settings=_probe_settings(max_response_bytes=1024),
            session=large,
        )
    assert large_error.value.details["redirect_count"] == 0

    media = _FakeSession([_FakeResponse(content_type="video/mp4")])
    with pytest.raises(UnsafeUrlError, match="unsupported_content_type"):
        site_probe._requests_fetch_text(
            "https://www.bilibili.com/video/BV1",
            {},
            10,
            allowed_host_suffixes=("bilibili.com",),
            settings=_probe_settings(),
            session=media,
        )

def test_secure_probe_allows_in_adapter_redirect_and_reports_count():
    session = _FakeSession([
        _FakeResponse(location="https://www.bilibili.com/video/BV1"),
        _FakeResponse(chunks=[b"<title>redirected</title>"]),
    ])

    text, _content_type, final_url, security = site_probe._requests_fetch_text(
        "https://b23.tv/example",
        {},
        10,
        allowed_host_suffixes=("bilibili.com", "b23.tv"),
        settings=_probe_settings(),
        session=session,
    )

    assert text == "<title>redirected</title>"
    assert final_url == "https://www.bilibili.com/video/BV1"
    assert security["redirect_count"] == 1


def test_secure_probe_blocks_fourth_redirect_with_security_details():
    session = _FakeSession([
        _FakeResponse(location="https://www.bilibili.com/video/1"),
        _FakeResponse(location="https://www.bilibili.com/video/2"),
        _FakeResponse(location="https://www.bilibili.com/video/3"),
        _FakeResponse(location="https://www.bilibili.com/video/4"),
    ])

    with pytest.raises(UnsafeUrlError, match="too_many_redirects") as error:
        site_probe._requests_fetch_text(
            "https://b23.tv/example",
            {},
            10,
            allowed_host_suffixes=("bilibili.com", "b23.tv"),
            settings=_probe_settings(),
            session=session,
        )

    assert error.value.details["redirect_count"] == 3
    assert len(session.calls) == 4