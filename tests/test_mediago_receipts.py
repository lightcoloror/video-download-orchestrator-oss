from __future__ import annotations

from video_orchestrator.runtime.mediago import import_to_mediago
from video_orchestrator.runtime.mediago import mediago_task_status


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.text = ""

    def json(self):
        return self._payload


def test_import_receipt_keeps_task_ids_but_drops_media_url_and_headers():
    def fake_post(*args, **kwargs):
        return FakeResponse(
            {
                "data": [
                    {
                        "id": 41,
                        "name": "合成任务",
                        "type": "direct",
                        "status": "pending",
                        "url": "https://cdn.example.test/video.mp4?token=secret",
                        "headers": "Authorization: secret",
                    }
                ]
            }
        )

    result = import_to_mediago(
        ["https://cdn.example.test/video.mp4?token=secret"],
        request_post=fake_post,
    )

    receipt = result["task_receipt"]
    assert receipt["contract"] == "vdo_mediago_import_receipt_v1"
    assert receipt["task_ids"] == [41]
    assert receipt["tasks"][0]["status"] == "pending"
    assert "url" not in receipt["tasks"][0]
    assert "headers" not in receipt["tasks"][0]
    assert "secret" not in str(receipt)


def test_task_status_reconciles_success_to_output_file():
    calls: list[str] = []

    def fake_get(url, **kwargs):
        calls.append(url)
        if url.endswith("/api/downloads/41"):
            return FakeResponse({"data": {"id": 41, "name": "合成任务", "status": "success", "url": "SECRET"}})
        if url.endswith("/api/config"):
            return FakeResponse({"data": {"local": "D:/fixture-downloads"}})
        return FakeResponse(
            {
                "data": {
                    "list": [
                        {
                            "id": 41,
                            "name": "合成任务",
                            "status": "success",
                            "exists": True,
                            "file": "D:/fixture-downloads/合成任务.mp4",
                            "url": "SECRET",
                        }
                    ]
                }
            }
        )

    result = mediago_task_status(41, request_get=fake_get)

    assert result["status"] == "ok"
    assert result["download_status"] == "success"
    assert result["media_exists"] is True
    assert result["output_path"].endswith("合成任务.mp4")
    assert "SECRET" not in str(result)
    assert len(calls) == 3


def test_task_status_failure_does_not_probe_output_directory():
    calls: list[str] = []

    def fake_get(url, **kwargs):
        calls.append(url)
        return FakeResponse({"data": {"id": 42, "name": "失败任务", "status": "failed", "url": "SECRET"}})

    result = mediago_task_status(42, request_get=fake_get)

    assert result["download_status"] == "failed"
    assert result["media_exists"] is False
    assert "重新执行" in result["next_action"]
    assert "SECRET" not in str(result)
    assert len(calls) == 1
