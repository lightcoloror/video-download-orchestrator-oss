from video_orchestrator.fallbacks import fallback_backends_for, fallback_plan_for_url


def test_general_url_falls_back_from_ytdlp_to_lux():
    assert fallback_backends_for("yt-dlp") == ["lux"]


def test_ytdlp_api_falls_back_to_cli_ytdlp_then_lux():
    assert fallback_backends_for("yt-dlp-api") == ["yt-dlp", "lux"]


def test_gallery_url_falls_back_to_ytdlp():
    plan = fallback_plan_for_url("https://www.tumblr.com/example/post/123")

    assert plan["selected_backend"] == "gallery-dl"
    assert plan["fallback_backends"] == ["yt-dlp"]


def test_live_url_falls_back_to_ytdlp():
    plan = fallback_plan_for_url("https://www.twitch.tv/example")

    assert plan["selected_backend"] == "streamlink"
    assert plan["fallback_backends"] == ["yt-dlp"]


def test_manifest_url_falls_back_to_ytdlp():
    plan = fallback_plan_for_url("https://example.com/live/master.m3u8")

    assert plan["selected_backend"] == "n_m3u8dl_re"
    assert plan["fallback_backends"] == ["vsd", "yt-dlp-api", "yt-dlp"]


def test_magnet_url_has_no_incompatible_http_or_ytdlp_fallback():
    plan = fallback_plan_for_url("magnet:?xt=urn:btih:abc123")

    assert plan["selected_backend"] == "aria2"
    assert plan["fallback_backends"] == []


def test_http_direct_transfer_falls_back_through_python_http():
    plan = fallback_plan_for_url("https://example.com/video.mp4")

    assert plan["selected_backend"] == "aria2"
    assert plan["fallback_backends"] == ["curl", "python-http", "yt-dlp"]


def test_spotify_url_falls_back_to_ytdlp_api():
    plan = fallback_plan_for_url("https://open.spotify.com/track/example")

    assert plan["selected_backend"] == "spotdl"
    assert plan["fallback_backends"] == ["yt-dlp-api"]
