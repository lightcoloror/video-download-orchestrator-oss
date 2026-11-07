from video_orchestrator.runtime.batch_cleaner import clean_batch_input
from video_orchestrator.runtime.batch_cleaner import normalize_batch_url


def test_clean_batch_input_extracts_youtube_links_from_mixed_text():
    raw = """
    - https://www.youtube.com/watch?v=XKZM_AjCUr0&list=PLqybz7NWybwUgR-S6m78tfd-lV4sBvGFG&index=1
    - https://youtu.be/AbJWIknEA-8?si=MxRoD9h2mBJ6aHY-
    - https://youtu.be/AbJWIknEA-8?si=Fp-NfAbWqkpDUddd
    - https://www.youtube.com/redirect?event=comments&q=https%3A%2F%2Fexample.com%2Fexternal&html_redirect=1
    """

    payload = clean_batch_input(raw)

    assert payload["contract"] == "agent_batch_clean_v1"
    assert payload["item_count"] == 2
    assert [item["url"] for item in payload["items"]] == [
        "https://www.youtube.com/watch?v=XKZM_AjCUr0",
        "https://www.youtube.com/watch?v=AbJWIknEA-8",
    ]
    assert {item["reason"] for item in payload["skipped"]} == {"duplicate", "youtube_redirect_external"}


def test_clean_batch_input_preserves_backend_objects():
    payload = clean_batch_input({"items": [{"url": "https://example.com/live.m3u8?utm_source=x", "backend": "n_m3u8dl_re"}]})

    assert payload["items"][0]["url"] == "https://example.com/live.m3u8"
    assert payload["items"][0]["backend"] == "n_m3u8dl_re"


def test_clean_batch_input_auto_summarizes_audio_links():
    payload = clean_batch_input(
        "播客简介 https://cdn.example.com/episodes/one.mp3\n"
        "重复链接 https://cdn.example.com/episodes/one.mp3\n"
        "无扩展页面 https://example.com/article"
    )

    assert payload["item_count"] == 2
    assert payload["skipped_count"] == 1
    assert payload["items"][0]["media_kind"] == "audio"
    assert payload["media_kinds"] == {"audio": 1, "unknown": 1}


def test_clean_batch_input_splits_concatenated_markdown_urls_and_deduplicates_target():
    raw = (
        "[https://elevenlabs.io/music/songs/71a8jhEdqGD9QPOsPt30"
        "https://elevenlabs.io/music/songs/ZzG1zHa5G7VRs4ATYjZt"
        "https://elevenlabs.io/music/songs/K1pZi1ARjKRKDD6wYuUt"
        "https://elevenlabs.io/music/songs/05w0PsOYwV9EPs5VUXXS]"
        "(https://elevenlabs.io/music/songs/71a8jhEdqGD9QPOsPt30"
        "https://elevenlabs.io/music/songs/ZzG1zHa5G7VRs4ATYjZt"
        "https://elevenlabs.io/music/songs/K1pZi1ARjKRKDD6wYuUt"
        "https://elevenlabs.io/music/songs/05w0PsOYwV9EPs5VUXXS)"
    )

    payload = clean_batch_input(raw)

    assert payload["input_count"] == 8
    assert payload["item_count"] == 4
    assert payload["skipped_count"] == 4
    assert payload["media_kinds"] == {"audio": 4}
    assert [item["url"] for item in payload["items"]] == [
        "https://elevenlabs.io/music/songs/71a8jhEdqGD9QPOsPt30",
        "https://elevenlabs.io/music/songs/ZzG1zHa5G7VRs4ATYjZt",
        "https://elevenlabs.io/music/songs/K1pZi1ARjKRKDD6wYuUt",
        "https://elevenlabs.io/music/songs/05w0PsOYwV9EPs5VUXXS",
    ]
    assert {item["reason"] for item in payload["skipped"]} == {"duplicate"}


def test_clean_batch_input_extracts_and_deduplicates_magnets_by_exact_topic():
    payload = clean_batch_input(
        "课程一 magnet:?xt=urn:btih:0123456789ABCDEF0123456789ABCDEF01234567&dn=Course-One\n"
        "同一资源 magnet:?dn=Course-Two&xt=urn:btih:0123456789abcdef0123456789abcdef01234567&tr=https%3A%2F%2Ftracker.example.test"
    )

    assert payload["input_count"] == 2
    assert payload["item_count"] == 1
    assert payload["skipped_count"] == 1
    assert payload["items"][0]["url"].startswith("magnet:?xt=urn:btih:")
    assert payload["skipped"][0]["reason"] == "duplicate"


def test_clean_batch_input_rejects_magnet_without_exact_topic():
    payload = clean_batch_input("magnet:?dn=Missing-Hash")

    assert payload["item_count"] == 0
    assert payload["skipped"][0]["reason"] == "invalid_magnet_uri"


def test_normalize_youtube_defaults_to_single_video():
    url, reason = normalize_batch_url("https://youtu.be/_0TY3cD90p8?si=tracking")

    assert reason == "youtube_short_url"
    assert url == "https://www.youtube.com/watch?v=_0TY3cD90p8"
