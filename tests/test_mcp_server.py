import json

from video_orchestrator.mcp_server import call_tool
from video_orchestrator.mcp_server import handle_request
from video_orchestrator.mcp_server import list_tools
from video_orchestrator.runtime.task_store import TaskStore


def _tool_payload(response):
    text = response["result"]["content"][0]["text"]
    return json.loads(text)


def test_mcp_lists_planning_only_tools():
    names = {tool["name"] for tool in list_tools()}

    assert names >= {
        "vdo_config_check",
        "vdo_backends",
        "vdo_route",
        "vdo_plan_download",
        "vdo_batch_plan",
        "vdo_list_tasks",
        "vdo_get_task",
        "vdo_read_artifact",
        "vdo_operator_status",
        "vdo_task_retryable",
        "vdo_task_review",
        "vdo_task_resume_plan",
        "vdo_handoff",
        "vdo_task_handoff",
        "vdo_task_vkp_handoff",
    }
    assert "vdo_download_execute" not in names
    assert "vdo_openclaw_execute" not in names


def test_mcp_initialize_and_tools_list_json_rpc():
    initialize = handle_request({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    tools = handle_request({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})

    assert initialize["result"]["serverInfo"]["name"] == "video-download-orchestrator"
    assert tools["result"]["tools"]


def test_mcp_plan_download_writes_redacted_manifest(tmp_path):
    response = handle_request(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "vdo_plan_download",
                "arguments": {
                    "url": "https://example.com/video?token=secret",
                    "output_dir": str(tmp_path / "downloads"),
                },
            },
        }
    )

    payload = _tool_payload(response)
    assert payload["status"] == "planned"
    assert payload["will_download"] is False
    assert payload["manifest"]["url"] == "https://example.com/video?token=%3Credacted%3E"
    assert payload["manifest_path"].endswith(".json")
    assert "secret" not in json.dumps(payload)


def test_mcp_batch_plan_does_not_execute(tmp_path):
    payload = call_tool(
        "vdo_batch_plan",
        {
            "output_dir": str(tmp_path / "batch"),
            "items": [
                {"url": "https://example.com/a"},
                {"url": "https://example.com/live/master.m3u8", "backend": "n_m3u8dl_re"},
            ],
        },
    )

    assert payload["contract"] == "agent_batch_v1"
    assert payload["execute"] is False
    assert payload["item_count"] == 2
    assert all(item["will_download"] is False for item in payload["items"])


def test_mcp_task_read_tools_include_artifacts(tmp_path):
    db_path = tmp_path / "tasks.sqlite"
    artifact_path = tmp_path / "downloads" / ".vdo" / "reports" / "1" / "report.md"
    artifact_path.parent.mkdir(parents=True)
    artifact_path.write_text("# report", encoding="utf-8")
    store = TaskStore(db_path)
    task_id = store.create_task("https://example.com/video", "yt-dlp")
    store.mark_failed(task_id, 1)
    store.add_artifact(task_id, "report", artifact_path)

    listed = call_tool("vdo_list_tasks", {"db_path": str(db_path), "problem_only": True})
    detail = call_tool("vdo_get_task", {"db_path": str(db_path), "task_id": task_id})

    assert listed["tasks"][0]["id"] == task_id
    assert detail["artifacts"]["report"].endswith("report.md")


def test_mcp_read_artifact_requires_vdo_path(tmp_path):
    plain = tmp_path / "plain.txt"
    plain.write_text("nope", encoding="utf-8")

    response = handle_request(
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {"name": "vdo_read_artifact", "arguments": {"path": str(plain)}},
        }
    )

    assert response["error"]["code"] == -32000
    assert ".vdo" in response["error"]["message"]


def test_mcp_operator_status_and_retryable_tasks(tmp_path):
    db_path = tmp_path / "tasks.sqlite"
    store = TaskStore(db_path)
    task_id = store.create_task("https://example.com/watch", "browser-sniffer")
    store.mark_retryable(task_id)

    status = call_tool("vdo_operator_status", {"db_path": str(db_path)})
    retryable = call_tool("vdo_task_retryable", {"db_path": str(db_path)})

    assert status["will_download"] is False
    assert status["retryable_task_count"] == 1
    assert retryable["tasks"][0]["id"] == task_id


def test_mcp_task_review_returns_verdict(tmp_path):
    db_path = tmp_path / "tasks.sqlite"
    review_path = tmp_path / "downloads" / ".vdo" / "reports" / "1" / "review-checklist.json"
    review_path.parent.mkdir(parents=True)
    review_path.write_text(
        json.dumps({"checks": [{"name": "output_files", "status": "ok"}], "backend_result": {"size_matches_page": True}}),
        encoding="utf-8",
    )
    store = TaskStore(db_path)
    task_id = store.create_task("https://example.com/video", "yt-dlp")
    store.mark_finished(task_id, 0)
    store.add_artifact(task_id, "review_checklist", review_path)

    payload = call_tool("vdo_task_review", {"db_path": str(db_path), "task_id": task_id})

    assert payload["will_download"] is False
    assert payload["verdict"] == "pass"
    assert payload["review"]["backend_result"]["size_matches_page"] is True


def test_mcp_task_resume_plan_is_planning_only(tmp_path):
    db_path = tmp_path / "tasks.sqlite"
    store = TaskStore(db_path)
    task_id = store.create_task("https://example.com/watch", "browser-sniffer")
    store.mark_retryable(task_id)

    retryable = call_tool("vdo_task_resume_plan", {"db_path": str(db_path), "task_id": task_id})
    planned = call_tool(
        "vdo_task_resume_plan",
        {
            "db_path": str(db_path),
            "task_id": task_id,
            "media_url": "https://cdn.example.com/video/file.mp4",
            "backend": "curl",
            "output_dir": str(tmp_path / "downloads"),
        },
    )

    assert retryable["status"] == "retryable"
    assert retryable["will_download"] is False
    assert planned["status"] == "planned"
    assert planned["will_download"] is False
    assert planned["manifest"]["resumed_task_id"] == task_id


def test_mcp_handoff_tool_returns_planning_payload():
    payload = call_tool("vdo_handoff", {"url": "https://cdn.example.com/video.mp4"})

    assert payload["will_download"] is False
    assert payload["recommended_ui"]["name"] == "motrix-next"


def test_mcp_task_handoff_tool_returns_planning_payload(tmp_path):
    db_path = tmp_path / "tasks.sqlite"
    store = TaskStore(db_path)
    task_id = store.create_task("https://example.com/watch", "browser-sniffer")
    store.mark_retryable(task_id)

    payload = call_tool("vdo_task_handoff", {"db_path": str(db_path), "task_id": task_id})

    assert payload["will_download"] is False
    assert payload["task"]["id"] == task_id
    assert "task resume" in payload["commands"]["task_resume_dry_run"]


def test_mcp_task_vkp_handoff_tool_returns_planning_payload(tmp_path):
    db_path = tmp_path / "tasks.sqlite"
    media = tmp_path / "downloads" / "video.mp4"
    media.parent.mkdir(parents=True)
    media.write_bytes(b"video")
    review_path = tmp_path / "downloads" / ".vdo" / "reports" / "1" / "review-checklist.json"
    review_path.parent.mkdir(parents=True)
    review_path.write_text(
        json.dumps({"checks": [{"name": "output_files", "status": "ok"}], "backend_result": {"output_file": str(media), "size_matches_page": True}}),
        encoding="utf-8",
    )
    store = TaskStore(db_path)
    task_id = store.create_task("https://example.com/video.mp4", "curl")
    store.mark_finished(task_id, 0)
    store.add_artifact(task_id, "review_checklist", review_path)

    payload = call_tool("vdo_task_vkp_handoff", {"db_path": str(db_path), "task_id": task_id, "output_dir": str(tmp_path / "handoffs")})

    assert payload["contract"] == "vdo_vkp_handoff_v1"
    assert payload["will_download"] is False
    assert payload["execute"] is False
    assert payload["media"]["media_exists"] is True
    assert payload["human_summary"]["headline"] == "VKP 交接包已准备好。"
    assert payload["vkp_ingest_suggestion"]["will_execute"] is False
