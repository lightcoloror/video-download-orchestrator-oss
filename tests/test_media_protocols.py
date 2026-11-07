from video_orchestrator.runtime.media_protocols import browser_protocols
from video_orchestrator.runtime.media_protocols import infer_media_kind
from video_orchestrator.runtime.media_protocols import match_media_protocol


def test_runtime_protocol_table_classifies_hls_and_direct_media():
    hls = match_media_protocol("https://cdn.example.com/master.m3u8", "application/vnd.apple.mpegurl")
    mov = match_media_protocol("https://cdn.example.com/video.mov", "video/quicktime")

    assert hls is not None
    assert hls["type"] == "HLS"
    assert hls["category"] == "stream"
    assert mov is not None
    assert mov["category"] == "files"


def test_audio_protocols_cover_direct_extensions_and_generic_audio_mime():
    mp3 = match_media_protocol("https://cdn.example.com/episode.mp3")
    generic = match_media_protocol("https://cdn.example.com/play?id=1", "audio/x-custom-codec")

    assert mp3 is not None
    assert mp3["media_kind"] == "audio"
    assert generic is not None
    assert generic["type"] == "AUDIO"
    assert infer_media_kind("https://cdn.example.com/play?id=1", "audio/x-custom-codec") == "audio"
    assert infer_media_kind("https://cdn.example.com/episode.ogg") == "audio"
    assert infer_media_kind("https://cdn.example.com/video.ogv") == "video"
    assert infer_media_kind("https://cdn.example.com/radio.m3u8", "audio/vnd.apple.mpegurl") == "audio"


def test_generated_browser_protocol_contract_contains_media_kind_and_audio_prefix():
    protocols = browser_protocols()

    assert any(item["mediaKind"] == "audio" for item in protocols)
    assert any("audio/" in item["contentTypePrefixes"] for item in protocols)
