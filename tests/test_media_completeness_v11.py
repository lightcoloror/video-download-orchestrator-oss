from datetime import UTC, datetime

import video_orchestrator.runtime.media_completeness as completeness


def test_hls_media_playlist_reports_complete_structure():
    payload = completeness.parse_hls_manifest_text(
        "#EXTM3U\n#EXT-X-TARGETDURATION:60\n#EXT-X-KEY:METHOD=AES-128,URI=key.bin\n#EXT-X-BYTERANGE:1000@0\n#EXTINF:60,\nsegment-1.ts\n#EXT-X-DISCONTINUITY\n#EXTINF:60,\nsegment-2.ts\n#EXT-X-ENDLIST\n"
    )

    assert payload["parser"] == "m3u8"
    assert payload["playlist_kind"] == "media"
    assert payload["has_endlist"] is True
    assert payload["segment_count"] == 2
    assert payload["duration_seconds"] == 120
    assert payload["has_encryption"] is True
    assert payload["has_byterange"] is True
    assert payload["discontinuity_count"] == 1
    assert payload["risk"] == "likely_complete"


def test_hls_master_playlist_reports_variants():
    payload = completeness.parse_hls_manifest_text(
        "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=800000,RESOLUTION=640x360\nlow.m3u8\n#EXT-X-STREAM-INF:BANDWIDTH=2500000,RESOLUTION=1920x1080\nhigh.m3u8\n"
    )

    assert payload["playlist_kind"] == "master"
    assert payload["variant_count"] == 2
    assert payload["max_bandwidth"] == 2500000
    assert payload["max_height"] == 1080


def test_hls_without_endlist_is_live_or_unfinished():
    payload = completeness.parse_hls_manifest_text("#EXTM3U\n#EXTINF:90,\nlive.ts\n")

    assert payload["risk"] == "live_or_unfinished"
    assert "ENDLIST" in payload["risk_reasons"][0]


def test_hls_parser_falls_back_when_dependency_is_unavailable(monkeypatch):
    monkeypatch.setattr(completeness, "m3u8", None)

    payload = completeness.parse_hls_manifest_text("#EXTM3U\n#EXTINF:90,\nvideo.ts\n#EXT-X-ENDLIST\n")

    assert payload["parser"] == "fallback_missing_dependency"
    assert payload["playlist_kind"] == "media"
    assert payload["risk"] == "likely_complete"

def test_hls_short_playlist_is_marked_as_suspected_preview():
    payload = completeness.parse_hls_manifest_text("#EXTM3U\n#EXTINF:20,\npreview.ts\n#EXT-X-ENDLIST\n")

    assert payload["risk"] == "suspected_preview"
    assert payload["quality_label"] == "疑似试看/片段"

def test_hls_master_selects_highest_video_variant_without_returning_uri():
    payload = completeness.parse_hls_manifest_text(
        "#EXTM3U\n"
        "#EXT-X-STREAM-INF:BANDWIDTH=256000,CODECS=\"mp4a.40.2\"\naudio.m3u8?token=secret\n"
        "#EXT-X-STREAM-INF:BANDWIDTH=3500000,RESOLUTION=1920x1080,FRAME-RATE=60\nfast.m3u8?token=secret\n"
        "#EXT-X-STREAM-INF:BANDWIDTH=4500000,RESOLUTION=1920x1080,FRAME-RATE=30\nhigh.m3u8?token=secret\n"
        "#EXT-X-STREAM-INF:BANDWIDTH=1500000,RESOLUTION=1280x720,FRAME-RATE=60\nmedium.m3u8\n"
    )

    assert payload["selected_variant_index"] == 2
    assert payload["selected_variant"]["width"] == 1920
    assert payload["selected_variant"]["bandwidth"] == 4500000
    assert payload["selected_variant"]["audio_only"] is False
    assert "audio.m3u8" not in str(payload)
    assert "fast.m3u8" not in str(payload)
    assert "high.m3u8" not in str(payload)
    assert "secret" not in str(payload)


def test_audio_only_hls_master_selects_highest_bitrate_variant():
    payload = completeness.parse_hls_manifest_text(
        "#EXTM3U\n"
        "#EXT-X-STREAM-INF:BANDWIDTH=96000,CODECS=\"mp4a.40.2\"\nlow-audio.m3u8?token=secret\n"
        "#EXT-X-STREAM-INF:BANDWIDTH=256000,CODECS=\"mp4a.40.2\"\nhigh-audio.m3u8?token=secret\n"
    )

    assert payload["playlist_media_kind"] == "audio"
    assert payload["selected_variant_index"] == 1
    assert payload["selected_variant"]["media_kind"] == "audio"
    assert "码率最高" in payload["selected_variant_reason"]
    assert "high-audio.m3u8" not in str(payload)
    assert "secret" not in str(payload)


def test_downloaded_audio_requires_audio_track_but_not_video_track():
    sniffed = {
        "url": "https://cdn.example.com/episode.m4a",
        "media_type": "AAC",
        "media_kind": "audio",
    }
    passed = completeness.downloaded_completeness_check(
        sniffed_media=sniffed,
        ffprobe_results=[
            {
                "returncode": 0,
                "metadata": {"format": {"duration": "180"}, "streams": [{"codec_type": "audio"}]},
            }
        ],
    )
    failed = completeness.downloaded_completeness_check(
        sniffed_media=sniffed,
        ffprobe_results=[
            {
                "returncode": 0,
                "metadata": {"format": {"duration": "180"}, "streams": [{"codec_type": "video"}]},
            }
        ],
    )

    assert passed["status"] == "ok"
    assert passed["has_video_stream"] is False
    assert failed["status"] == "failed"
    assert "音频轨道" in failed["message"]


def test_downloaded_unknown_page_infers_audio_from_ffprobe_streams():
    result = completeness.downloaded_completeness_check(
        sniffed_media={},
        ffprobe_results=[
            {
                "returncode": 0,
                "metadata": {
                    "format": {"duration": "30.0", "size": "488746"},
                    "streams": [{"codec_type": "audio", "codec_name": "aac"}],
                },
            }
        ],
    )

    assert result["status"] == "ok"
    assert result["media_kind"] == "audio"
    assert result["has_audio_stream"] is True
    assert result["has_video_stream"] is False
    assert "音频" in result["next_action"]


def test_complete_content_range_is_not_treated_as_preview():
    payload = completeness.sniffed_completeness_diagnostic(
        {
            "url": "https://v26-web.douyinvod.com/video/main.mp4",
            "content_range": "bytes 0-5334576/5334577",
            "content_length": 5_334_577,
        }
    )

    assert payload["range_kind"] == "complete"
    assert payload["range_coverage_ratio"] == 1
    assert payload["risk"] == "likely_complete"
    assert payload["resource_role"] == "primary"


def test_partial_content_range_remains_lower_priority():
    capture = {
        "url": "https://v26-web.douyinvod.com/video/main.mp4",
        "content_range": "bytes 7-204807/5334577",
        "content_length": 204_801,
    }
    payload = completeness.sniffed_completeness_diagnostic(capture)

    assert payload["range_kind"] == "partial"
    assert payload["risk"] == "partial_capture"
    assert completeness.capture_selection_summary(capture)["reason"] == "partial_range"


def test_short_form_video_passes_when_full_size_and_streams_are_valid():
    sniffed = {
        "url": "https://v26-web.douyinvod.com/video/main.mp4",
        "media_type": "MP4",
        "content_range": "bytes 0-5334576/5334577",
        "content_length": 5_334_577,
    }
    result = completeness.downloaded_completeness_check(
        sniffed_media=sniffed,
        ffprobe_results=[
            {
                "returncode": 0,
                "metadata": {
                    "format": {"duration": "38.866667", "size": "5334577"},
                    "streams": [
                        {"codec_type": "video", "width": 1080, "height": 1920},
                        {"codec_type": "audio"},
                    ],
                },
            }
        ],
    )

    assert result["status"] == "ok"
    assert result["ffprobe_duration_seconds"] == 38.866667
    assert result["has_video_stream"] is True
    assert result["has_audio_stream"] is True


def test_downloaded_file_fails_when_expected_total_size_does_not_match():
    result = completeness.downloaded_completeness_check(
        sniffed_media={
            "url": "https://cdn.example.com/main.mp4",
            "media_type": "MP4",
            "content_range": "bytes 0-9999999/10000000",
        },
        ffprobe_results=[
            {
                "returncode": 0,
                "metadata": {
                    "format": {"duration": "12", "size": "5000000"},
                    "streams": [{"codec_type": "video"}],
                },
            }
        ],
    )

    assert result["status"] == "failed"
    assert result["expected_size_bytes"] == 10_000_000
    assert "大小" in result["message"]


def test_effect_cdn_candidate_is_classified_as_auxiliary_media():
    payload = completeness.sniffed_completeness_diagnostic(
        {
            "url": "https://lf3-effectcdn-tos.byteeffecttos.com/obj/ies.fe.effect/example.mp4",
            "content_length": 226_971,
        }
    )

    assert payload["risk"] == "auxiliary_asset"
    assert payload["resource_role"] == "auxiliary"


def test_signed_url_lifecycle_reports_expiry_without_returning_query_values():
    payload = completeness.signed_url_lifecycle(
        "https://cdn.example.com/main.mp4?token=secret&expires=1700000000",
        now=datetime(2026, 1, 1, tzinfo=UTC),
    )

    assert payload["signed_url_likely"] is True
    assert payload["expired"] is True
    assert "secret" not in str(payload)
