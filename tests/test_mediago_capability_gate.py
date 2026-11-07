from fastapi.testclient import TestClient

from video_orchestrator.api import app
from video_orchestrator.runtime.mediago import build_mediago_handoff_plan
from video_orchestrator.runtime.mediago import mediago_task_capability


DOUYIN_URL = "https://www.douyin.com/video/1234567890"
client = TestClient(app)


def test_douyin_page_is_not_misrepresented_as_youtube():
    capability = mediago_task_capability(DOUYIN_URL)
    plan = build_mediago_handoff_plan([DOUYIN_URL])

    assert capability["supported"] is False
    assert plan["task_count"] == 0
    assert plan["payload"]["tasks"] == []
    assert plan["blocked_count"] == 1
    assert plan["blocked_items"][0]["recommended_path"] == "browser_sniffer_then_direct_or_m3u8"
    assert "youtube" not in str(plan["payload"])


def test_sniffed_douyin_direct_media_can_still_use_mediago_direct_type():
    plan = build_mediago_handoff_plan(["https://media.example.test/video/final.mp4"])

    assert plan["task_count"] == 1
    assert plan["payload"]["tasks"][0]["type"] == "direct"


def test_direct_audio_keeps_mediago_payload_compatible_and_adds_summary():
    plan = build_mediago_handoff_plan(["https://media.example.test/audio/episode.mp3"])

    assert plan["payload"]["tasks"][0]["type"] == "direct"
    assert set(plan["payload"]["tasks"][0]) == {"type", "name", "url", "headers", "folder"}
    assert plan["media_summary"]["counts"] == {"audio": 1}
    assert plan["task_summaries"][0]["media_label"] == "音频"


def test_manual_m4s_is_direct_not_a_page_sniffing_candidate():
    from video_orchestrator.runtime.mediago import mediago_page_candidates

    url = "https://cdn.example.test/video/track.m4s?deadline=123&upsig=fixture-only"
    plan = build_mediago_handoff_plan([url])

    assert plan["will_download"] is False
    assert plan["payload"]["startDownload"] is False
    assert plan["payload"]["tasks"][0]["type"] == "direct"
    assert plan["payload"]["tasks"][0]["url"] == url
    assert plan["task_summaries"][0]["url"] == "https://cdn.example.test/video/track.m4s"
    assert mediago_page_candidates([url], plan["payload"]["tasks"]) == []


def test_mediago_task_summary_drops_entire_ephemeral_query():
    plan = build_mediago_handoff_plan(
        ["https://media.example.test/audio/episode.m4a?unknown-signature-name=secret&plain=value"]
    )

    assert plan["task_summaries"][0]["url"] == "https://media.example.test/audio/episode.m4a"
    assert "secret" not in str(plan["task_summaries"])


def test_magnet_uses_mediago_torrent_type_and_display_name():
    magnet = (
        "magnet:?XT=urn:btih:0123456789abcdef0123456789abcdef01234567"
        "&DN=Fixture%20Course&tr=https%3A%2F%2Ftracker.example.test%2Fannounce"
    )

    capability = mediago_task_capability(magnet)
    plan = build_mediago_handoff_plan([magnet])

    assert capability["supported"] is True
    assert capability["mediago_type"] == "torrent"
    assert capability["recommended_path"] == "mediago_torrent_import"
    assert plan["payload"]["tasks"][0]["type"] == "torrent"
    assert plan["payload"]["tasks"][0]["name"] == "Fixture Course"
    assert "tracker.example.test" not in plan["task_summaries"][0]["url"]


def test_remote_torrent_url_uses_mediago_torrent_type():
    plan = build_mediago_handoff_plan(["https://example.test/releases/fixture.torrent?download=1"])

    assert plan["payload"]["tasks"][0]["type"] == "torrent"
    assert plan["payload"]["tasks"][0]["name"] == "fixture"


def test_mixed_plan_keeps_supported_items_and_reports_blocked_douyin_page():
    plan = build_mediago_handoff_plan(
        [
            DOUYIN_URL,
            "https://media.example.test/video/final.mp4",
        ]
    )

    assert plan["task_count"] == 1
    assert plan["blocked_count"] == 1
    assert plan["payload"]["tasks"][0]["type"] == "direct"


def test_mediago_http_plan_explains_blocked_douyin_capability():
    response = client.post("/external-uis/mediago/plan", json={"url": DOUYIN_URL})

    assert response.status_code == 200
    payload = response.json()
    assert payload["will_download"] is False
    assert payload["blocked_count"] == 1
    assert payload["summary"]["blocked_count"] == 1
    assert "能力准入阻止 1 项" in payload["summary"]["label"]
