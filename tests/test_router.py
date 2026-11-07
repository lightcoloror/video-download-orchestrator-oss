from video_orchestrator.router import classify_url


def test_classifies_hls_manifest():
    assert classify_url("https://example.com/live/master.m3u8").kind == "manifest"


def test_classifies_dash_manifest():
    assert classify_url("https://example.com/video/manifest.mpd").kind == "manifest"


def test_classifies_youtube_as_general_video():
    result = classify_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ")

    assert result.kind == "general"
    assert result.preferred_backend == "yt-dlp-api"
    assert result.reason == "default general-purpose downloader"


def test_classifies_twitch_live_as_streamlink_candidate():
    result = classify_url("https://www.twitch.tv/example")

    assert result.kind == "live"
    assert result.preferred_backend == "streamlink"


def test_classifies_magnet_as_direct_transfer():
    result = classify_url("magnet:?xt=urn:btih:abc123")

    assert result.kind == "direct-transfer"
    assert result.preferred_backend == "aria2"


def test_classifies_ed2k_as_direct_transfer():
    result = classify_url("ed2k://|file|example.mp4|123|abc|/")

    assert result.kind == "direct-transfer"
    assert result.preferred_backend == "aria2"


def test_classifies_thunder_as_direct_transfer():
    result = classify_url("thunder://QUFodHRwOi8vZXhhbXBsZS5jb20vZmlsZS5tcDRaWg==")

    assert result.kind == "direct-transfer"
    assert result.preferred_backend == "aria2"


def test_classifies_torrent_url_as_direct_transfer():
    result = classify_url("https://example.com/download/file.torrent")

    assert result.kind == "direct-transfer"
    assert result.preferred_backend == "aria2"


def test_classifies_direct_media_file_as_direct_transfer():
    result = classify_url("https://cdn.example.com/videos/file.mp4")

    assert result.kind == "direct-transfer"
    assert result.preferred_backend == "aria2"


def test_classifies_direct_audio_and_spotify_as_audio():
    direct = classify_url("https://cdn.example.com/podcast/episode.flac")
    spotify = classify_url("https://open.spotify.com/track/example")

    assert direct.kind == "direct-transfer"
    assert direct.media_kind == "audio"
    assert spotify.media_kind == "audio"


def test_classifies_elevenlabs_music_page_as_audio():
    result = classify_url("https://elevenlabs.io/music/songs/example-song-id")

    assert result.kind == "elevenlabs-music-page"
    assert result.preferred_backend == "yt-dlp-api"
    assert result.media_kind == "audio"


def test_classifies_feishu_drive_stream_as_direct_transfer():
    result = classify_url(
        "https://internal-api-drive-stream.feishu.cn/space/api/box/stream/download/video/example/?quality=1080p"
    )

    assert result.kind == "direct-transfer"
    assert result.preferred_backend == "aria2"


def test_classifies_feishu_page_as_site_adapter():
    result = classify_url("https://my.feishu.cn/wiki/TMfewUw6AiqjVbkhxfGcBDNjnGc")

    assert result.kind == "feishu-page"
    assert result.preferred_backend == "feishu"
    assert "Feishu page" in result.reason


def test_classifies_tencent_meeting_recording_share():
    result = classify_url(
        "https://meeting.tencent.com/meeting-record/shares?id=b01e10d8-99f5-4eb3-9f7e-4f20f21a9aba&from=3"
    )

    assert result.kind == "tencent-meeting"
    assert result.preferred_backend == "tencent-meeting"
    assert "Tencent Meeting" in result.reason


def test_classifies_spotify_as_spotdl_candidate():
    result = classify_url("https://open.spotify.com/track/example")

    assert result.kind == "spotify"
    assert result.preferred_backend == "spotdl"


def test_classifies_platform_pages_as_yt_dlp_first_adapters():
    cases = [
        ("https://www.bilibili.com/video/BV1xx411c7mD", "bilibili-page"),
        ("https://b23.tv/example", "bilibili-page"),
        ("https://www.xiaohongshu.com/explore/example", "xiaohongshu-page"),
        ("https://xhslink.com/example", "xiaohongshu-page"),
        ("https://www.douyin.com/video/1234567890", "douyin-page"),
        ("https://v.douyin.com/example", "douyin-page"),
        ("https://www.ixigua.com/video/1234567890", "douyin-page"),
        ("https://www.toutiao.com/video/1234567890", "douyin-page"),
    ]

    for url, kind in cases:
        result = classify_url(url)

        assert result.kind == kind
        assert result.preferred_backend == "yt-dlp-api"
