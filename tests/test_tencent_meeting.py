import json

from video_orchestrator.backends.tencent_meeting import extract_tencent_meeting_context
from video_orchestrator.backends.tencent_meeting import fetch_signed_media_url


def test_extract_tencent_meeting_context_from_next_server_data():
    server_data = {
        "total_recording_size": "381949940",
        "allow_download": False,
        "meeting_info": {"meeting_id": "meeting-1"},
        "recordings": [{"sharing_id": "record-share-1", "id": "recording-1", "size": "123"}],
    }
    html = f'<script>self.__next_f.push([1,"15:[[null,{{\\"data\\":{{}}}}]]"]); "serverData":{json.dumps(server_data)}</script>'

    context = extract_tencent_meeting_context("https://meeting.tencent.com/meeting-record/shares?id=page-share-1", html)

    assert context.share_id == "page-share-1"
    assert context.recording_share_id == "record-share-1"
    assert context.meeting_id == "meeting-1"
    assert context.recording_id == "recording-1"
    assert context.expected_size == 381949940
    assert context.allow_download is False


def test_fetch_signed_media_url_uses_recording_and_page_share_ids():
    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"code": 0, "signurl": "https://example.com/video.mp4?token=secret"}

    class FakeSession:
        def __init__(self):
            self.url = None
            self.headers = None

        def get(self, url, headers, timeout):
            self.url = url
            self.headers = headers
            return FakeResponse()

    context = extract_tencent_meeting_context(
        "https://meeting.tencent.com/meeting-record/shares?id=page-share-1",
        '<script>"serverData":{"recordings":[{"sharing_id":"record-share-1"}]}</script>',
    )
    session = FakeSession()

    media_url = fetch_signed_media_url(session, context, user_agent="UA", timeout=5)

    assert media_url == "https://example.com/video.mp4?token=secret"
    assert "id=record-share-1" in session.url
    assert "sharing_id=page-share-1" in session.url
    assert session.headers["Referer"] == "https://meeting.tencent.com/meeting-record/shares?id=page-share-1"
