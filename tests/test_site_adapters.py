from video_orchestrator.runtime.site_adapters import match_site_adapter
from video_orchestrator.runtime.site_adapters import site_adapter_diagnostic


def test_site_adapter_registry_matches_feishu_page():
    adapter = match_site_adapter("https://my.feishu.cn/wiki/example")

    assert adapter is not None
    assert adapter.name == "feishu"
    assert adapter.kind == "feishu-page"
    assert adapter.backend == "feishu"
    assert "site_fallback" in adapter.capabilities


def test_site_adapter_registry_matches_tencent_meeting():
    adapter = match_site_adapter("https://meeting.tencent.com/meeting-record/shares?id=share-id")

    assert adapter is not None
    assert adapter.name == "tencent-meeting"
    assert adapter.kind == "tencent-meeting"
    assert adapter.backend == "tencent-meeting"


def test_site_adapter_registry_matches_elevenlabs_music_as_audio():
    adapter = match_site_adapter("https://elevenlabs.io/music/songs/example-song-id")

    assert adapter is not None
    assert adapter.name == "elevenlabs-music"
    assert adapter.backend == "yt-dlp-api"
    assert adapter.media_kind == "audio"
    assert adapter.diagnostic(matched=True)["media_kind"] == "audio"


def test_site_adapter_diagnostic_for_general_url_is_stable():
    diagnostic = site_adapter_diagnostic("https://example.com/watch")

    assert diagnostic == {
        "matched": False,
        "name": None,
        "kind": None,
        "backend": None,
        "reason": "No registered site adapter matched this URL",
        "capabilities": [],
        "fallback_mode": "none",
        "supports_site_fallback": False,
    }


def test_platform_adapters_are_diagnostic_only_without_site_fallback():
    for url, name, kind in [
        ("https://www.bilibili.com/video/BV1xx411c7mD", "bilibili", "bilibili-page"),
        ("https://b23.tv/example", "bilibili", "bilibili-page"),
        ("https://www.xiaohongshu.com/explore/example", "xiaohongshu", "xiaohongshu-page"),
        ("https://xhslink.com/example", "xiaohongshu", "xiaohongshu-page"),
        ("https://www.douyin.com/video/1234567890", "douyin", "douyin-page"),
        ("https://v.douyin.com/example", "douyin", "douyin-page"),
        ("https://www.ixigua.com/1234567890", "douyin", "douyin-page"),
        ("https://www.toutiao.com/video/1234567890", "douyin", "douyin-page"),
    ]:
        diagnostic = site_adapter_diagnostic(url)

        assert diagnostic["matched"] is True
        assert diagnostic["name"] == name
        assert diagnostic["kind"] == kind
        assert diagnostic["backend"] == "yt-dlp-api"
        assert diagnostic["fallback_mode"] == "browser_sniffing"
        assert diagnostic["supports_site_fallback"] is False
        assert "public_page_probe" in diagnostic["capabilities"]
