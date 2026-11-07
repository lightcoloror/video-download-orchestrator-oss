import json
from datetime import UTC, datetime

from fastapi.testclient import TestClient

import video_orchestrator.api as api_module
from video_orchestrator.api import app
from video_orchestrator.runtime.sniff_store import read_recent_sniff_records
from video_orchestrator.runtime.config import api_base_url
from video_orchestrator.runtime.config import sniffer_plan_endpoint
from video_orchestrator.runtime.task_store import TaskStore


client = TestClient(app)


def setup_function():
    api_module.SNIFFED_MEDIA_CAPTURES.clear()
    api_module.SNIFFED_MEDIA_REJECTIONS.clear()


def test_api_health():
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_api_runtime_config():
    response = client.get("/config")

    assert response.status_code == 200
    payload = response.json()
    assert payload["api_base_url"] == api_base_url()
    assert payload["sniffer_plan_endpoint"] == sniffer_plan_endpoint()
    assert payload["execution"]["hard_timeout_seconds"] >= payload["execution"]["idle_timeout_seconds"]
    assert payload["check"]["status"] == "ok"


def test_external_uis_endpoint_is_planning_only():
    response = client.get("/external-uis")

    assert response.status_code == 200
    payload = response.json()
    assert payload["will_download"] is False
    assert {ui["name"] for ui in payload["uis"]} >= {"mediago", "metube", "hls-downloader", "motrix-next"}


def test_mediago_status_endpoint_is_planning_only(monkeypatch):
    def fake_mediago_status():
        return {
            "service": "video-download-orchestrator",
            "ui": "mediago",
            "status": "available",
            "will_download": False,
            "http_base": "http://127.0.0.1:39719",
            "next_action": "MediaGo 已可用",
        }

    monkeypatch.setattr(api_module, "mediago_status", fake_mediago_status)
    response = client.get("/external-uis/mediago/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ui"] == "mediago"
    assert payload["will_download"] is False


def test_mediago_task_status_endpoint_is_read_only(monkeypatch):
    def fake_task_status(task_id):
        return {
            "service": "video-download-orchestrator",
            "ui": "mediago",
            "status": "ok",
            "task_id": task_id,
            "download_status": "pending",
            "will_download": False,
        }

    monkeypatch.setattr(api_module, "mediago_task_status", fake_task_status)
    response = client.get("/external-uis/mediago/tasks/42/status")

    assert response.status_code == 200
    assert response.json()["task_id"] == 42
    assert response.json()["will_download"] is False


def test_mediago_plan_endpoint_builds_import_payload():
    response = client.post(
        "/external-uis/mediago/plan",
        json={
            "text": "https://youtu.be/abc123?si=tracking\nhttps://youtu.be/abc123?si=again",
            "output_dir": "downloads",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ui"] == "mediago"
    assert payload["status"] == "planned"
    assert payload["will_download"] is False
    assert payload["payload"]["startDownload"] is False
    assert payload["payload"]["tasks"][0]["type"] == "youtube"
    assert payload["payload"]["tasks"][0]["url"] == "https://www.youtube.com/watch?v=abc123"
    assert payload["item_count"] == 1
    assert payload["skipped_count"] == 1
    assert payload["type_counts"] == {"youtube": 1}
    assert payload["summary"]["type_counts"] == {"youtube": 1}
    assert payload["summary"]["type_labels"] == {"youtube": "YouTube"}
    assert payload["summary"]["skipped_reasons"] == {"duplicate": 1}
    assert payload["summary"]["skipped_reason_labels"] == {"duplicate": "重复"}
    assert payload["summary"]["strategy_counts"] == {"general_download": 1}
    assert payload["summary"]["strategy_labels"] == {"general_download": "通用下载器"}
    assert "可播放性" in payload["summary"]["quality_gate"]
    assert "识别 1 个可导入链接" in payload["summary"]["label"]
    assert "YouTube 1 个" in payload["summary"]["label"]
    assert payload["cleaning"]["skipped"][0]["reason"] == "duplicate"
    assert "/api/downloads" in payload["import_endpoint"]


def test_mediago_plan_endpoint_accepts_magnet_and_reports_peer_to_peer_strategy():
    response = client.post(
        "/external-uis/mediago/plan",
        json={
            "text": (
                "BT 资源 magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567"
                "&dn=Fixture%20Course"
            )
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["payload"]["tasks"][0]["type"] == "torrent"
    assert payload["summary"]["strategy_counts"] == {"peer_to_peer": 1}
    assert payload["summary"]["strategy_labels"]["peer_to_peer"] == "磁力 / BT 下载"


def test_mediago_import_endpoint_is_explicit_user_action(monkeypatch):
    def fake_import_to_mediago(urls, **kwargs):
        return {
            "service": "video-download-orchestrator",
            "ui": "mediago",
            "status": "imported",
            "will_download": bool(kwargs["start_download"]),
            "explicit_user_action": True,
            "task_count": len(list(urls)),
        }

    monkeypatch.setattr(api_module, "import_to_mediago", fake_import_to_mediago)
    response = client.post(
        "/external-uis/mediago/import",
        json={"text": "https://youtu.be/abc123\nhttps://youtu.be/abc123", "output_dir": "downloads"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "imported"
    assert payload["explicit_user_action"] is True
    assert payload["will_download"] is False
    assert payload["task_count"] == 1
    assert payload["item_count"] == 1
    assert payload["skipped_count"] == 1


def test_api_serves_web_ui():
    response = client.get("/")

    assert response.status_code == 200
    assert "视频下载调度器" in response.text
    assert "批量规划" in response.text
    assert "只看问题任务" in response.text
    assert "一键下载" in response.text
    assert "导入 MediaGo" in response.text
    assert "批量导入 MediaGo" in response.text
    assert "/external-uis/mediago/import" in response.text
    assert "清洗批量输入" in response.text
    assert "自动批量下载" in response.text
    assert "/batch/clean" in response.text
    assert "/batch/execute" in response.text
    assert "提取方式" in response.text
    assert "站点适配" in response.text
    assert "平台适配" in response.text
    assert "公开页面解析" in response.text
    assert "失败后建议页面嗅探" in response.text
    assert "页面嗅探" in response.text
    assert "runtimeConfig" in response.text
    assert "规划重跑" in response.text
    assert "质量结论" in response.text
    assert "重新执行" in response.text
    assert "页面嗅探恢复" in response.text
    assert "标记通过" in response.text
    assert "生成 VKP 交接包" in response.text
    assert "/vkp-handoff" in response.text
    assert "这里只写 manifest，不下载，也不调用 VKP" in response.text
    assert "summaryPanel" in response.text
    assert "选择任务或点击操作后，这里会显示中文摘要。" in response.text
    assert "检查清单发现失败项" in response.text
    assert "返回码" in response.text
    assert "下载计划" in response.text
    assert "查看原始数据" in response.text
    assert "/download/execute" in response.text
    assert "/download/page/execute" in response.text
    assert "/site-adapter/probe" in response.text
    assert "/operator/tasks/" in response.text
    assert "/review" in response.text
    assert "/rerun/execute" in response.text
    assert "/resume/execute" in response.text
    assert "等待播放/捕获" in response.text
    assert "manifest" in response.text
    assert "review_checklist" in response.text


def test_api_route():
    response = client.get("/route", params={"url": "https://example.com/live/master.m3u8"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["will_download"] is False
    assert payload["route"]["preferred_backend"] == "n_m3u8dl_re"
    assert payload["site_adapter"]["matched"] is False


def test_api_route_includes_site_adapter_for_feishu():
    response = client.get("/route", params={"url": "https://my.feishu.cn/wiki/example"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["route"]["kind"] == "feishu-page"
    assert payload["site_adapter"]["matched"] is True
    assert payload["site_adapter"]["name"] == "feishu"
    assert payload["site_adapter"]["supports_site_fallback"] is True


def test_api_route_includes_platform_site_adapter():
    response = client.get("/route", params={"url": "https://www.bilibili.com/video/BV1xx411c7mD"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["route"]["kind"] == "bilibili-page"
    assert payload["route"]["preferred_backend"] == "yt-dlp-api"
    assert payload["site_adapter"]["matched"] is True
    assert payload["site_adapter"]["name"] == "bilibili"
    assert payload["site_adapter"]["supports_site_fallback"] is False


def test_api_site_adapter_probe_is_planning_only(monkeypatch):
    def fake_probe_public_page(url, *, headers=None, timeout_seconds=10):
        return {
            "service": "video-download-orchestrator",
            "status": "ok",
            "will_download": False,
            "url": url,
            "site_adapter": {"matched": True, "name": "bilibili"},
            "probe": {"probe_status": "ok", "title": "示例 B站视频"},
        }

    monkeypatch.setattr(api_module, "probe_public_page", fake_probe_public_page)
    response = client.post(
        "/site-adapter/probe",
        json={
            "url": "https://www.bilibili.com/video/BV1xx411c7mD",
            "headers": {"Cookie": "session=secret"},
            "timeout_seconds": 3,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["will_download"] is False
    assert payload["site_adapter"]["name"] == "bilibili"
    assert payload["probe"]["probe_status"] == "ok"


def test_api_download_plan(tmp_path):
    response = client.post(
        "/download/plan",
        json={"url": "https://example.com/video", "output_dir": str(tmp_path / "downloads")},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "planned"
    assert payload["will_download"] is False
    assert payload["manifest"]["selected_backend"] == "yt-dlp-api"
    assert payload["manifest_path"].endswith(".json")


def test_api_download_plan_passes_explicit_ytdlp_ejs_options(tmp_path):
    response = client.post(
        "/download/plan",
        json={
            "url": "https://www.youtube.com/watch?v=abc",
            "output_dir": str(tmp_path / "downloads"),
            "cookies": str(tmp_path / "youtube-cookies.txt"),
            "js_runtimes": "node",
            "remote_components": ["ejs:github"],
        },
    )

    assert response.status_code == 200
    command = response.json()["manifest"]["command"]
    assert "--cookies" in command
    assert command[command.index("--cookies") + 1] == str(tmp_path / "youtube-cookies.txt")
    assert command[command.index("--js-runtimes") + 1] == "node"
    assert command[command.index("--remote-components") + 1] == "ejs:github"


def test_api_download_execute_is_explicit_user_action(tmp_path, monkeypatch):
    def fake_execute_manifest(**kwargs):
        task_id = kwargs["store"].create_task(str(kwargs["manifest"]["url"]), str(kwargs["manifest"]["selected_backend"]))
        kwargs["store"].mark_finished(task_id, 0)
        return {
            "service": "video-download-orchestrator",
            "status": "finished",
            "selected_backend": kwargs["manifest"]["selected_backend"],
            "task": kwargs["store"].get_task(task_id),
            "artifacts": {"manifest_path": str(kwargs["manifest_path"])},
        }

    monkeypatch.setattr(api_module, "execute_manifest", fake_execute_manifest)
    response = client.post(
        "/download/execute",
        json={
            "url": "https://example.com/video.mp4",
            "output_dir": str(tmp_path / "downloads"),
            "db_path": str(tmp_path / "tasks.sqlite"),
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "finished"
    assert payload["will_download"] is True
    assert payload["explicit_user_action"] is True
    assert payload["task"]["status"] == "finished"


def test_api_download_page_execute_uses_sniffed_capture(tmp_path, monkeypatch):
    def fake_execute_manifest(**kwargs):
        task_id = kwargs["store"].create_task(str(kwargs["manifest"]["url"]), str(kwargs["manifest"]["selected_backend"]))
        kwargs["store"].mark_finished(task_id, 0)
        return {
            "service": "video-download-orchestrator",
            "status": "finished",
            "selected_backend": kwargs["manifest"]["selected_backend"],
            "task": kwargs["store"].get_task(task_id),
            "artifacts": {"manifest_path": str(kwargs["manifest_path"])},
            **(kwargs.get("extra_payload") or {}),
        }

    monkeypatch.setattr(api_module, "execute_manifest", fake_execute_manifest)
    client.post(
        "/sniffed-media/plan",
        json={
            "url": "https://cdn.example.com/video/file.mp4",
            "page_url": "https://example.com/watch",
            "media_type": "MP4",
            "category": "files",
            "content_type": "video/mp4",
            "request_headers": {"Cookie": "session=secret"},
            "response_headers": {"content-type": "video/mp4"},
            "output_dir": str(tmp_path / "sniffed"),
        },
    )

    response = client.post(
        "/download/page/execute",
        json={
            "url": "https://example.com/watch",
            "output_dir": str(tmp_path / "downloads"),
            "db_path": str(tmp_path / "tasks.sqlite"),
            "timeout_seconds": 1,
            "poll_interval": 0,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "finished"
    assert payload["will_download"] is True
    assert payload["explicit_user_action"] is True
    assert payload["page_url"] == "https://example.com/watch"
    assert payload["sniff_status"] == "captured"
    assert payload["selected_backend"] == "curl"
    assert payload["capture"]["request_headers"]["Cookie"] == "<redacted>"


def test_api_download_page_execute_dry_run_only_plans(tmp_path):
    client.post(
        "/sniffed-media/plan",
        json={
            "url": "https://cdn.example.com/video/file.mp4",
            "page_url": "https://example.com/watch",
            "media_type": "MP4",
            "category": "files",
            "content_type": "video/mp4",
            "response_headers": {"content-type": "video/mp4"},
            "output_dir": str(tmp_path / "sniffed"),
        },
    )

    response = client.post(
        "/download/page/execute",
        json={
            "url": "https://example.com/watch",
            "output_dir": str(tmp_path / "downloads"),
            "db_path": str(tmp_path / "tasks.sqlite"),
            "timeout_seconds": 1,
            "poll_interval": 0,
            "dry_run": True,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "planned"
    assert payload["will_download"] is False
    assert payload["dry_run"] is True
    assert payload["manifest"]["source_capture_id"]
    assert payload["manifest_path"].endswith(".json")


def test_api_download_page_execute_timeout_is_user_readable():
    response = client.post(
        "/download/page/execute",
        json={
            "url": "https://example.com/watch",
            "timeout_seconds": 0,
            "poll_interval": 0,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "timeout"
    assert payload["will_download"] is False
    assert payload["explicit_user_action"] is True
    assert payload["site_adapter"]["matched"] is False
    assert "点击播放视频" in payload["next_action"]


def test_api_download_page_execute_preserves_site_specific_fallback(tmp_path):
    response = client.post(
        "/download/page/execute",
        json={
            "url": "https://my.feishu.cn/wiki/example",
            "output_dir": str(tmp_path / "downloads"),
            "timeout_seconds": 0,
            "poll_interval": 0,
            "dry_run": True,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "planned"
    assert payload["will_download"] is False
    assert payload["site_specific_fallback"] is True
    assert payload["site_adapter"]["name"] == "feishu"
    assert payload["manifest"]["selected_backend"] == "feishu"


def test_api_batch_plan(tmp_path):
    response = client.post(
        "/batch/plan",
        json={
            "output_dir": str(tmp_path / "batch"),
            "items": [
                {"url": "https://example.com/video"},
                {"url": "https://example.com/live/master.m3u8", "backend": "n_m3u8dl_re"},
            ],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["contract"] == "agent_batch_v1"
    assert payload["execute"] is False
    assert payload["item_count"] == 2
    assert payload["items"][1]["backend"] == "n_m3u8dl_re"
    assert payload["cleaning"]["item_count"] == 2


def test_api_batch_clean_from_mixed_text():
    response = client.post(
        "/batch/clean",
        json={
            "text": "https://youtu.be/H5pnAd3wLl0?si=tracking\nhttps://youtu.be/H5pnAd3wLl0?si=again",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["will_download"] is False
    assert payload["item_count"] == 1
    assert payload["items"][0]["url"] == "https://www.youtube.com/watch?v=H5pnAd3wLl0"
    assert payload["skipped"][0]["reason"] == "duplicate"


def test_api_openclaw_link_plan_cleans_and_routes_mixed_text(tmp_path):
    response = client.post(
        "/integrations/openclaw/link/plan",
        json={
            "text": "视频简介\nhttps://youtu.be/H5pnAd3wLl0?si=one\n重复 https://youtu.be/H5pnAd3wLl0?si=two\nhttps://example.com/file.mp4?token=secret",
            "output_dir": str(tmp_path / "openclaw"),
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["contract"] == "openclaw_telegram_link_plan_v1"
    assert payload["will_download"] is False
    assert payload["explicit_user_action_required_for_download"] is True
    assert payload["item_count"] == 2
    assert payload["cleaning"]["skipped"][0]["reason"] == "duplicate"
    assert payload["items"][0]["route"]["kind"] == "general"
    assert payload["items"][0]["needs_browser_sniffing_if_direct_fails"] is True
    assert payload["items"][1]["route"]["kind"] == "direct-transfer"
    assert payload["items"][1]["url"].endswith("token=%3Credacted%3E")
    assert payload["items"][1]["manifest_path"]


def test_api_openclaw_link_plan_non_video_page_stays_planning_only(tmp_path):
    response = client.post(
        "/integrations/openclaw/link/plan",
        json={
            "url": "https://example.com/article",
            "output_dir": str(tmp_path / "openclaw"),
            "write_manifests": False,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["will_download"] is False
    assert payload["batch_dir"] is None
    assert payload["items"][0]["route"]["kind"] == "general"
    assert payload["items"][0]["extraction_method"] == "普通下载尝试；失败后页面嗅探"
    assert "不要自动下载" in payload["execution_boundary"]["forbidden"]


def test_api_openclaw_telegram_execute_blocks_unlisted_actor(tmp_path, monkeypatch):
    monkeypatch.setenv("VDO_TELEGRAM_ALLOWED_USER_IDS", "42")
    response = client.post(
        "/integrations/openclaw/telegram/execute",
        json={
            "url": "https://example.com/video.mp4",
            "actor_id": "99",
            "output_dir": str(tmp_path / "openclaw"),
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "blocked"
    assert payload["will_download"] is False
    assert payload["actor"]["reason"] == "actor_not_allowed"


def test_api_openclaw_telegram_execute_requires_confirmation(tmp_path, monkeypatch):
    monkeypatch.setenv("VDO_TELEGRAM_ALLOWED_USER_IDS", "42")
    response = client.post(
        "/integrations/openclaw/telegram/execute",
        json={
            "url": "https://example.com/video.mp4",
            "actor_id": "42",
            "output_dir": str(tmp_path / "openclaw"),
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "confirmation_required"
    assert payload["will_download"] is False
    assert payload["summary"]["item_count"] == 1
    assert "链接 1 个" in payload["summary"]["label"]


def test_api_openclaw_telegram_execute_requires_sensitive_confirmation(tmp_path, monkeypatch):
    monkeypatch.setenv("VDO_TELEGRAM_ALLOWED_USER_IDS", "42")
    response = client.post(
        "/integrations/openclaw/telegram/execute",
        json={
            "url": "https://example.com/article",
            "actor_id": "42",
            "confirm": True,
            "output_dir": str(tmp_path / "openclaw"),
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "sensitive_confirmation_required"
    assert payload["will_download"] is False
    assert payload["summary"]["requires_sensitive_confirmation"] is True


def test_api_openclaw_telegram_execute_runs_after_confirm(tmp_path, monkeypatch):
    monkeypatch.setenv("VDO_TELEGRAM_ALLOWED_USER_IDS", "42")
    calls = []

    def fake_execute_manifest(**kwargs):
        calls.append(kwargs)
        task_id = kwargs["store"].create_task(str(kwargs["manifest"]["url"]), str(kwargs["manifest"]["selected_backend"]))
        kwargs["store"].mark_finished(task_id, 0)
        return {
            "service": "video-download-orchestrator",
            "status": "finished",
            "task": kwargs["store"].get_task(task_id),
            "artifacts": {"manifest_path": str(kwargs["manifest_path"])},
        }

    monkeypatch.setattr(api_module, "execute_manifest", fake_execute_manifest)
    response = client.post(
        "/integrations/openclaw/telegram/execute",
        json={
            "url": "https://example.com/video.mp4",
            "actor_id": "42",
            "approval_command": "/vdo_execute confirm",
            "request_id": "telegram-api-success-001",
            "requested_at": datetime.now(UTC).isoformat(),
            "confirm": True,
            "output_dir": str(tmp_path / "openclaw"),
            "db_path": str(tmp_path / "tasks.sqlite"),
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "finished"
    assert payload["will_download"] is True
    assert payload["execute"] is True
    assert payload["actor"]["authorized"] is True
    assert calls[0]["extra_payload"]["openclaw_approval"]["actor_id"] == "42"
    assert calls[0]["manifest"]["source"] == "openclaw_telegram_execute"


def test_api_allows_mediago_localhost_batch_clean_cors():
    response = client.options(
        "/batch/clean",
        headers={
            "Origin": "http://127.0.0.1:39719",
            "Access-Control-Request-Method": "POST",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://127.0.0.1:39719"


def test_api_batch_execute_cleans_mixed_text_and_downloads(tmp_path, monkeypatch):
    calls = []

    def fake_execute_manifest(**kwargs):
        calls.append(kwargs["manifest"])
        task_id = kwargs["store"].create_task(str(kwargs["manifest"]["url"]), str(kwargs["manifest"]["selected_backend"]))
        kwargs["store"].mark_finished(task_id, 0)
        return {
            "service": "video-download-orchestrator",
            "status": "finished",
            "selected_backend": kwargs["manifest"]["selected_backend"],
            "task": kwargs["store"].get_task(task_id),
            "artifacts": {"manifest_path": str(kwargs["manifest_path"])},
        }

    monkeypatch.setattr(api_module, "execute_manifest", fake_execute_manifest)
    response = client.post(
        "/batch/execute",
        json={
            "text": "简介\n00:01 开场\nhttps://youtu.be/H5pnAd3wLl0?si=one\nhttps://youtu.be/H5pnAd3wLl0?si=two\nhttps://example.com/live.m3u8",
            "output_dir": str(tmp_path / "downloads"),
            "db_path": str(tmp_path / "tasks.sqlite"),
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "finished"
    assert payload["execute"] is True
    assert payload["will_download"] is True
    assert payload["explicit_user_action"] is True
    assert payload["item_count"] == 2
    assert payload["cleaning"]["skipped"][0]["reason"] == "duplicate"
    assert [manifest["batch_item_index"] for manifest in calls] == [1, 2]
    assert payload["items"][0]["url"] == "https://www.youtube.com/watch?v=H5pnAd3wLl0"


def test_api_lists_tasks_with_artifacts(tmp_path):
    db_path = tmp_path / "tasks.sqlite"
    report_path = tmp_path / "downloads" / ".vdo" / "reports" / "1" / "report.md"
    report_path.parent.mkdir(parents=True)
    report_path.write_text("# report", encoding="utf-8")
    store = TaskStore(db_path)
    task_id = store.create_task("https://example.com/video", "yt-dlp")
    store.mark_failed(task_id, 1)
    store.add_artifact(task_id, "report", report_path)

    response = client.get("/tasks", params={"db_path": str(db_path), "problem_only": True})

    assert response.status_code == 200
    payload = response.json()
    assert payload["problem_only"] is True
    assert payload["tasks"][0]["id"] == task_id
    assert payload["tasks"][0]["artifacts"]["report"].endswith("report.md")


def test_api_reads_artifact(tmp_path):
    artifact_path = tmp_path / ".vdo" / "logs" / "1.log"
    artifact_path.parent.mkdir(parents=True)
    artifact_path.write_text("backend log", encoding="utf-8")

    response = client.get("/artifacts", params={"path": str(artifact_path)})

    assert response.status_code == 200
    assert response.text == "backend log"


def test_api_rejects_non_vdo_artifact(tmp_path):
    artifact_path = tmp_path / "plain.txt"
    artifact_path.write_text("nope", encoding="utf-8")

    response = client.get("/artifacts", params={"path": str(artifact_path)})

    assert response.status_code == 400


def test_api_accepts_task(tmp_path):
    db_path = tmp_path / "tasks.sqlite"
    store = TaskStore(db_path)
    task_id = store.create_task("https://example.com/video", "yt-dlp")
    store.mark_finished(task_id, 0)

    response = client.post(f"/tasks/{task_id}/accept", json={"db_path": str(db_path)})

    assert response.status_code == 200
    assert response.json()["task"]["status"] == "accepted"


def test_api_rerun_plan_writes_versioned_manifest(tmp_path):
    db_path = tmp_path / "tasks.sqlite"
    store = TaskStore(db_path)
    task_id = store.create_task("https://example.com/video", "yt-dlp")

    response = client.post(
        f"/tasks/{task_id}/rerun/plan",
        json={"db_path": str(db_path), "output_dir": str(tmp_path / "downloads")},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "planned"
    assert payload["will_download"] is False
    assert "/.vdo/reruns/" in payload["rerun_dir"].replace("\\", "/")
    assert payload["manifest"]["rerun_of_task_id"] == task_id


def test_operator_status_endpoint_reports_retryable_tasks(tmp_path):
    db_path = tmp_path / "tasks.sqlite"
    store = TaskStore(db_path)
    task_id = store.create_task("https://example.com/watch", "browser-sniffer")
    store.mark_retryable(task_id)

    response = client.get("/operator/status", params={"db_path": str(db_path)})

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["retryable_task_count"] == 1
    assert payload["retryable_tasks"][0]["id"] == task_id
    assert payload["will_download"] is False


def test_operator_task_review_endpoint_returns_verdict(tmp_path):
    db_path = tmp_path / "tasks.sqlite"
    store = TaskStore(db_path)
    task_id = store.create_task("https://example.com/video", "yt-dlp")
    store.mark_finished(task_id, 0)
    review_path = tmp_path / "downloads" / ".vdo" / "reports" / str(task_id) / "review-checklist.json"
    review_path.parent.mkdir(parents=True)
    review_path.write_text(
        json.dumps({"status": "ok", "checks": [{"name": "output_files", "status": "ok"}], "backend_result": {"size_matches_page": True}}),
        encoding="utf-8",
    )
    store.add_artifact(task_id, "review_checklist", review_path)

    response = client.get(f"/operator/tasks/{task_id}/review", params={"db_path": str(db_path)})

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["verdict"] == "pass"
    assert payload["quality_status"] == "pass"
    assert payload["quality_label"] == "可标记通过"
    assert payload["reason_labels"] == ["输出文件检查通过"]
    assert payload["can_accept"] is True
    assert payload["can_rerun"] is False
    assert payload["can_resume"] is False
    assert payload["task"]["id"] == task_id
    assert payload["will_download"] is False


def test_api_rerun_execute_runs_versioned_manifest(tmp_path, monkeypatch):
    def fake_execute_manifest(**kwargs):
        task_id = kwargs["store"].create_task(str(kwargs["manifest"]["url"]), str(kwargs["manifest"]["selected_backend"]))
        kwargs["store"].mark_finished(task_id, 0)
        return {
            "service": "video-download-orchestrator",
            "status": "finished",
            "selected_backend": kwargs["manifest"]["selected_backend"],
            "task": kwargs["store"].get_task(task_id),
            "artifacts": {"manifest_path": str(kwargs["manifest_path"])},
        }

    monkeypatch.setattr(api_module, "execute_manifest", fake_execute_manifest)
    db_path = tmp_path / "tasks.sqlite"
    store = TaskStore(db_path)
    original_task_id = store.create_task("https://example.com/video", "yt-dlp")
    store.mark_failed(original_task_id, 1)

    response = client.post(
        f"/tasks/{original_task_id}/rerun/execute",
        json={"db_path": str(db_path), "output_dir": str(tmp_path / "downloads")},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "finished"
    assert payload["will_download"] is True
    assert payload["explicit_user_action"] is True
    assert payload["original_task"]["id"] == original_task_id
    assert "/.vdo/reruns/" in payload["rerun_dir"].replace("\\", "/")
    assert payload["task"]["id"] != original_task_id


def test_operator_handoff_endpoint_is_planning_only():
    response = client.get("/operator/handoff", params={"url": "https://example.com/video.mp4"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "planned"
    assert payload["will_download"] is False
    assert payload["recommended_ui"]["name"] == "motrix-next"


def test_operator_task_handoff_endpoint_includes_task(tmp_path):
    db_path = tmp_path / "tasks.sqlite"
    store = TaskStore(db_path)
    task_id = store.create_task("https://example.com/watch", "browser-sniffer")
    store.mark_retryable(task_id)

    response = client.get(f"/operator/tasks/{task_id}/handoff", params={"db_path": str(db_path)})

    assert response.status_code == 200
    payload = response.json()
    assert payload["task"]["id"] == task_id
    assert payload["will_download"] is False
    assert "task resume" in payload["commands"]["task_resume_dry_run"]


def test_operator_task_vkp_handoff_endpoint_is_planning_only(tmp_path):
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

    response = client.get(
        f"/operator/tasks/{task_id}/vkp-handoff",
        params={"db_path": str(db_path), "output_dir": str(tmp_path / "handoffs")},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["contract"] == "vdo_vkp_handoff_v1"
    assert payload["will_download"] is False
    assert payload["execute"] is False
    assert payload["media"]["media_exists"] is True
    assert payload["human_summary"]["headline"] == "VKP 交接包已准备好。"
    assert payload["vkp_ingest_suggestion"]["will_execute"] is False
    assert payload["handoff_manifest_path"].endswith("task-1-vkp-handoff.json")


def test_operator_task_resume_plan_is_planning_only(tmp_path):
    db_path = tmp_path / "tasks.sqlite"
    store = TaskStore(db_path)
    task_id = store.create_task("https://example.com/watch", "browser-sniffer")
    store.mark_retryable(task_id)
    client.post(
        "/sniffed-media/plan",
        json={
            "url": "https://cdn.example.com/video/file.mp4",
            "page_url": "https://example.com/watch",
            "capture_session_id": "api-resume-test",
            "media_type": "MP4",
            "category": "files",
            "content_type": "video/mp4",
            "response_headers": {"content-type": "video/mp4"},
            "output_dir": str(tmp_path / "sniffed"),
        },
    )

    response = client.post(
        f"/operator/tasks/{task_id}/resume/plan",
        json={
            "db_path": str(db_path),
            "output_dir": str(tmp_path / "downloads"),
            "capture_session_id": "api-resume-test",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "planned"
    assert payload["will_download"] is False
    assert payload["resumed_task"]["id"] == task_id
    assert payload["manifest"]["source_capture_id"]
    assert payload["manifest"]["sniffed_media"]["capture_session_id"] == "api-resume-test"
    assert payload["manifest_path"].endswith(".json")


def test_operator_task_resume_execute_runs_capture(tmp_path, monkeypatch):
    def fake_execute_manifest(**kwargs):
        task_id = kwargs["store"].create_task(str(kwargs["manifest"]["url"]), str(kwargs["manifest"]["selected_backend"]))
        kwargs["store"].mark_finished(task_id, 0)
        return {
            "service": "video-download-orchestrator",
            "status": "finished",
            "selected_backend": kwargs["manifest"]["selected_backend"],
            "task": kwargs["store"].get_task(task_id),
            "artifacts": {"manifest_path": str(kwargs["manifest_path"])},
        }

    monkeypatch.setattr(api_module, "execute_manifest", fake_execute_manifest)
    db_path = tmp_path / "tasks.sqlite"
    store = TaskStore(db_path)
    original_task_id = store.create_task("https://example.com/watch", "browser-sniffer")
    store.mark_retryable(original_task_id)
    client.post(
        "/sniffed-media/plan",
        json={
            "url": "https://cdn.example.com/video/file.mp4",
            "page_url": "https://example.com/watch",
            "capture_session_id": "api-resume-execute",
            "media_type": "MP4",
            "category": "files",
            "content_type": "video/mp4",
            "response_headers": {"content-type": "video/mp4"},
            "output_dir": str(tmp_path / "sniffed"),
        },
    )

    response = client.post(
        f"/operator/tasks/{original_task_id}/resume/execute",
        json={
            "db_path": str(db_path),
            "output_dir": str(tmp_path / "downloads"),
            "capture_session_id": "api-resume-execute",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "finished"
    assert payload["will_download"] is True
    assert payload["explicit_user_action"] is True
    assert payload["resumed_task"]["id"] == original_task_id
    assert payload["capture_page_match"] == "exact"
    assert "/.vdo/resumes/" in payload["resume_dir"].replace("\\", "/")
    assert payload["capture"]["capture_session_id"] == "api-resume-execute"


def test_operator_task_resume_execute_without_capture_returns_retryable(tmp_path):
    db_path = tmp_path / "tasks.sqlite"
    store = TaskStore(db_path)
    task_id = store.create_task("https://example.com/watch", "browser-sniffer")
    store.mark_retryable(task_id)

    response = client.post(
        f"/operator/tasks/{task_id}/resume/execute",
        json={"db_path": str(db_path), "output_dir": str(tmp_path / "downloads")},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "retryable"
    assert payload["will_download"] is False
    assert payload["explicit_user_action"] is True
    assert "播放视频" in payload["next_action"]


def test_api_sniffed_media_plan_accepts_hls_manifest(tmp_path):
    response = client.post(
        "/sniffed-media/plan",
        json={
            "url": "https://cdn.example.com/live/master.m3u8",
            "page_url": "https://example.com/watch",
            "page_title": "Example video",
            "initiator": "https://example.com/watch",
            "content_type": "application/vnd.apple.mpegurl",
            "headers": {"content-type": "application/vnd.apple.mpegurl"},
            "output_dir": str(tmp_path / "downloads"),
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "planned"
    assert payload["will_download"] is False
    assert payload["manifest"]["selected_backend"] == "n_m3u8dl_re"
    assert payload["manifest"]["source"] == "browser_sniffer"
    assert payload["manifest"]["sniffed_media"]["page_title"] == "Example video"
    assert payload["quality_score"] > 90
    assert payload["manifest"]["sniffed_media"]["download_candidate_reason"] == "stream manifest"


def test_api_sniffed_media_plan_uses_vsd_for_authenticated_manifest(tmp_path):
    response = client.post(
        "/sniffed-media/plan",
        json={
            "url": "https://cdn.example.com/live/master.m3u8",
            "request_id": "123",
            "media_type": "HLS",
            "category": "stream",
            "page_url": "https://example.com/watch",
            "content_type": "application/vnd.apple.mpegurl",
            "request_headers": {
                "Referer": "https://example.com/watch",
                "Cookie": "session=secret",
            },
            "response_headers": {"content-type": "application/vnd.apple.mpegurl"},
            "tab_id": 3,
            "frame_id": 0,
            "resource_type": "xmlhttprequest",
            "method": "GET",
            "status_code": 200,
            "output_dir": str(tmp_path / "downloads"),
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["manifest"]["selected_backend"] == "vsd"
    assert payload["manifest"]["sniffed_media"]["tab_id"] == 3
    assert payload["manifest"]["sniffed_media"]["download_candidate_reason"]
    assert payload["manifest"]["request_headers"]["Cookie"] == "<redacted>"
    assert "session=secret" not in str(payload)


def test_api_latest_sniffed_media_defaults_to_redacted_headers(tmp_path):
    response = client.post(
        "/sniffed-media/plan",
        json={
            "url": "https://cdn.example.com/live/master.m3u8",
            "page_url": "https://example.com/watch",
            "category": "stream",
            "content_type": "application/vnd.apple.mpegurl",
            "request_headers": {"Cookie": "session=secret"},
            "response_headers": {"content-type": "application/vnd.apple.mpegurl"},
            "output_dir": str(tmp_path / "downloads"),
        },
    )

    assert response.status_code == 200
    latest = client.get("/sniffed-media/latest", params={"page_url": "https://example.com/watch"}).json()

    assert latest["status"] == "ok"
    assert latest["capture_count"] == 1
    assert latest["rejection_count"] == 0
    assert latest["capture_page_match"] == "exact"
    assert latest["latest_rejection"] is None
    assert latest["capture"]["request_headers"]["Cookie"] == "<redacted>"
    assert "session=secret" not in str(latest)


def test_api_latest_sniffed_media_can_return_sensitive_headers_for_local_cli(tmp_path):
    client.post(
        "/sniffed-media/plan",
        json={
            "url": "https://cdn.example.com/live/master.m3u8",
            "page_url": "https://example.com/watch",
            "category": "stream",
            "content_type": "application/vnd.apple.mpegurl",
            "request_headers": {"Cookie": "session=secret"},
            "response_headers": {"content-type": "application/vnd.apple.mpegurl"},
            "output_dir": str(tmp_path / "downloads"),
        },
    )

    latest = client.get(
        "/sniffed-media/latest",
        params={"page_url": "https://example.com/watch", "include_sensitive": True},
    ).json()

    assert latest["status"] == "ok"
    assert latest["capture"]["request_headers"]["Cookie"] == "session=secret"


def test_sniffed_capture_diagnostics_are_persisted_redacted(tmp_path, monkeypatch):
    sniff_store_dir = tmp_path / ".vdo"
    monkeypatch.setenv("VDO_SNIFF_STORE_DIR", str(sniff_store_dir))
    response = client.post(
        "/sniffed-media/plan",
        json={
            "url": "https://cdn.example.com/video/file.mp4?token=secret",
            "page_url": "https://example.com/watch?session=secret",
            "category": "files",
            "content_type": "video/mp4",
            "request_headers": {"Cookie": "session=secret", "Referer": "https://example.com/watch"},
            "response_headers": {"content-type": "video/mp4", "set-cookie": "sid=secret"},
            "output_dir": str(tmp_path / "downloads"),
        },
    )

    assert response.status_code == 200
    persisted = read_recent_sniff_records("captures", base_dir=sniff_store_dir)

    assert len(persisted) == 1
    assert persisted[0]["url"] == "https://cdn.example.com/video/file.mp4"
    assert persisted[0]["request_headers"]["Cookie"] == "<redacted>"
    assert "secret" not in (sniff_store_dir / "sniffed-media.jsonl").read_text(encoding="utf-8")

    latest = client.get(
        "/sniffed-media/latest",
        params={"page_url": "https://example.com/watch?session=secret", "include_sensitive": True},
    ).json()
    assert latest["capture"]["request_headers"]["Cookie"] == "session=secret"


def test_sniffed_rejection_diagnostics_are_persisted_redacted(tmp_path, monkeypatch):
    sniff_store_dir = tmp_path / ".vdo"
    monkeypatch.setenv("VDO_SNIFF_STORE_DIR", str(sniff_store_dir))
    response = client.post(
        "/sniffed-media/plan",
        json={
            "url": "https://example.com/watch?token=secret",
            "page_url": "https://example.com/watch?session=secret",
            "content_type": "text/html",
            "request_headers": {"Cookie": "session=secret"},
            "response_headers": {"content-type": "text/html"},
            "output_dir": str(tmp_path / "downloads"),
        },
    )

    assert response.status_code == 400
    persisted = read_recent_sniff_records("rejections", base_dir=sniff_store_dir)

    assert len(persisted) == 1
    assert persisted[0]["url"] == "https://example.com/watch?token=%3Credacted%3E"
    assert persisted[0]["request_headers"]["Cookie"] == "<redacted>"
    assert "secret" not in (sniff_store_dir / "sniffed-media-rejections.jsonl").read_text(encoding="utf-8")


def test_api_sniffed_media_diagnostics_endpoint_is_redacted(tmp_path, monkeypatch):
    monkeypatch.setenv("VDO_SNIFF_STORE_DIR", str(tmp_path / ".vdo"))
    client.post(
        "/sniffed-media/plan",
        json={
            "url": "https://cdn.example.com/video/file.mp4?token=secret",
            "page_url": "https://example.com/watch?session=secret",
            "category": "files",
            "content_type": "video/mp4",
            "request_headers": {"Cookie": "session=secret"},
            "response_headers": {"content-type": "video/mp4"},
            "output_dir": str(tmp_path / "downloads"),
        },
    )

    response = client.get("/sniffed-media/diagnostics", params={"limit": 10})

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["captures"][0]["request_headers"]["Cookie"] == "<redacted>"
    assert "secret" not in str(payload)


def test_api_latest_sniffed_media_prefers_best_candidate_over_range_file(tmp_path):
    client.post(
        "/sniffed-media/plan",
        json={
            "url": "https://cdn.example.com/video/file.mp4?token=secret",
            "page_url": "https://example.com/watch",
            "media_type": "MP4",
            "category": "files",
            "content_type": "video/mp4",
            "response_headers": {"content-type": "video/mp4", "content-range": "bytes 0-99/999"},
            "content_range": "bytes 0-99/999",
            "output_dir": str(tmp_path / "downloads"),
        },
    )
    client.post(
        "/sniffed-media/plan",
        json={
            "url": "https://cdn.example.com/live/master.m3u8",
            "page_url": "https://example.com/watch",
            "media_type": "HLS",
            "category": "stream",
            "content_type": "application/vnd.apple.mpegurl",
            "response_headers": {"content-type": "application/vnd.apple.mpegurl"},
            "output_dir": str(tmp_path / "downloads"),
        },
    )

    latest = client.get("/sniffed-media/latest", params={"page_url": "https://example.com/watch"}).json()

    assert latest["status"] == "ok"
    assert latest["capture"]["url"] == "https://cdn.example.com/live/master.m3u8"
    assert latest["capture"]["quality_score"] > 90
    assert "secret" not in str(latest)


def test_api_latest_sniffed_media_prefers_full_candidate_over_preview(tmp_path):
    client.post(
        "/sniffed-media/plan",
        json={
            "url": "https://cdn.example.com/video/preview.mp4",
            "page_url": "https://example.com/course",
            "media_type": "MP4",
            "category": "files",
            "content_type": "video/mp4",
            "content_length": 50_000_000,
            "output_dir": str(tmp_path / "preview"),
        },
    )
    client.post(
        "/sniffed-media/plan",
        json={
            "url": "https://cdn.example.com/video/full/master.m3u8",
            "page_url": "https://example.com/course",
            "media_type": "HLS",
            "category": "stream",
            "content_type": "application/vnd.apple.mpegurl",
            "response_headers": {"content-type": "application/vnd.apple.mpegurl"},
            "output_dir": str(tmp_path / "full"),
        },
    )

    latest = client.get("/sniffed-media/latest", params={"page_url": "https://example.com/course"}).json()

    assert latest["status"] == "ok"
    assert latest["capture"]["url"] == "https://cdn.example.com/video/full/master.m3u8"
    assert latest["capture_count"] == 2
    assert len(latest["candidates"]) == 2
    assert latest["candidates"][1]["completeness"]["risk"] == "suspected_preview"


def test_api_latest_sniffed_media_matches_page_without_query_or_hash(tmp_path):
    response = client.post(
        "/sniffed-media/plan",
        json={
            "url": "https://cdn.example.com/live/master.m3u8",
            "page_url": "https://example.com/watch?reload=1#from-player",
            "category": "stream",
            "content_type": "application/vnd.apple.mpegurl",
            "response_headers": {"content-type": "application/vnd.apple.mpegurl"},
            "output_dir": str(tmp_path / "downloads"),
        },
    )

    assert response.status_code == 200
    latest = client.get("/sniffed-media/latest", params={"page_url": "https://example.com/watch?from=3"}).json()

    assert latest["status"] == "ok"
    assert latest["capture_page_match"] == "normalized"
    assert latest["capture_count"] == 1
    assert latest["capture"]["page_url"] == "https://example.com/watch?reload=1#from-player"


def test_api_latest_sniffed_media_can_filter_by_capture_session_id(tmp_path):
    for session_id, media_name in [("session-a", "a.m3u8"), ("session-b", "b.m3u8")]:
        response = client.post(
            "/sniffed-media/plan",
            json={
                "url": f"https://cdn.example.com/live/{media_name}",
                "page_url": "https://example.com/watch",
                "capture_session_id": session_id,
                "category": "stream",
                "content_type": "application/vnd.apple.mpegurl",
                "response_headers": {"content-type": "application/vnd.apple.mpegurl"},
                "output_dir": str(tmp_path / session_id),
            },
        )
        assert response.status_code == 200

    latest = client.get(
        "/sniffed-media/latest",
        params={"page_url": "https://example.com/watch", "capture_session_id": "session-b"},
    ).json()

    assert latest["status"] == "ok"
    assert latest["capture"]["capture_session_id"] == "session-b"
    assert latest["capture"]["url"] == "https://cdn.example.com/live/b.m3u8"


def test_api_latest_sniffed_media_prefers_exact_page_match_over_normalized(tmp_path):
    client.post(
        "/sniffed-media/plan",
        json={
            "url": "https://cdn.example.com/live/normalized.m3u8",
            "page_url": "https://example.com/watch?reload=1",
            "category": "stream",
            "content_type": "application/vnd.apple.mpegurl",
            "response_headers": {"content-type": "application/vnd.apple.mpegurl"},
            "output_dir": str(tmp_path / "downloads-a"),
        },
    )
    client.post(
        "/sniffed-media/plan",
        json={
            "url": "https://cdn.example.com/live/exact.m3u8",
            "page_url": "https://example.com/watch?from=3",
            "category": "stream",
            "content_type": "application/vnd.apple.mpegurl",
            "response_headers": {"content-type": "application/vnd.apple.mpegurl"},
            "output_dir": str(tmp_path / "downloads-b"),
        },
    )

    latest = client.get("/sniffed-media/latest", params={"page_url": "https://example.com/watch?from=3"}).json()

    assert latest["status"] == "ok"
    assert latest["capture_page_match"] == "exact"
    assert latest["capture_count"] == 1
    assert latest["capture"]["url"] == "https://cdn.example.com/live/exact.m3u8"


def test_api_sniffed_media_deduplicates_by_best_quality(tmp_path):
    low = client.post(
        "/sniffed-media/plan",
        json={
            "url": "https://cdn.example.com/video/file.mp4?range=1",
            "page_url": "https://example.com/watch",
            "media_type": "MP4",
            "category": "files",
            "content_type": "video/mp4",
            "content_range": "bytes 0-99/999",
            "dedupe_key": "same",
            "output_dir": str(tmp_path / "downloads"),
        },
    ).json()
    high = client.post(
        "/sniffed-media/plan",
        json={
            "url": "https://cdn.example.com/video/file.mp4",
            "page_url": "https://example.com/watch",
            "media_type": "MP4",
            "category": "files",
            "content_type": "video/mp4",
            "content_length": 5_000_000,
            "dedupe_key": "same",
            "output_dir": str(tmp_path / "downloads"),
        },
    ).json()

    latest = client.get("/sniffed-media/latest", params={"page_url": "https://example.com/watch"}).json()

    assert high["capture_id"] == low["capture_id"]
    assert latest["capture"]["url"] == "https://cdn.example.com/video/file.mp4"
    assert latest["capture"]["quality_score"] == high["quality_score"]


def test_api_sniffed_media_plan_accepts_direct_media_file(tmp_path):
    response = client.post(
        "/sniffed-media/plan",
        json={
            "url": "https://cdn.example.com/video/file.mp4",
            "media_type": "MP4",
            "category": "files",
            "request_headers": {"Referer": "https://example.com/watch"},
            "response_headers": {"content-type": "video/mp4"},
            "output_dir": str(tmp_path / "downloads"),
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["manifest"]["selected_backend"] == "curl"
    assert payload["manifest"]["sniffed_media"]["download_candidate_reason"]


def test_api_sniffed_media_auto_identifies_generic_audio_mime(tmp_path):
    response = client.post(
        "/sniffed-media/plan",
        json={
            "url": "https://cdn.example.com/play?id=episode-1",
            "page_url": "https://example.com/podcast/episode-1",
            "media_type": "AUDIO",
            "category": "files",
            "content_type": "audio/x-custom-codec",
            "response_headers": {"content-type": "audio/x-custom-codec"},
            "content_length": 8_000_000,
            "output_dir": str(tmp_path / "downloads"),
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["manifest"]["selected_backend"] == "curl"
    assert payload["manifest"]["media_kind"] == "audio"
    assert payload["manifest"]["sniffed_media"]["media_kind"] == "audio"
    assert "audio media identified" in payload["download_candidate_reason"]


def test_api_sniffed_media_plan_rejects_plain_page_url(tmp_path):
    response = client.post(
        "/sniffed-media/plan",
        json={
            "url": "https://example.com/watch?token=secret",
            "page_url": "https://example.com/watch?session=secret",
            "content_type": "text/html",
            "resource_type": "main_frame",
            "status_code": 200,
            "request_headers": {"Cookie": "session=secret"},
            "response_headers": {"content-type": "text/html"},
            "output_dir": str(tmp_path / "downloads"),
        },
    )

    assert response.status_code == 400
    latest = client.get("/sniffed-media/latest", params={"page_url": "https://example.com/watch?session=secret"}).json()

    assert latest["status"] == "not_found"
    assert latest["capture_count"] == 0
    assert latest["rejection_count"] == 1
    assert latest["rejection_page_match"] == "exact"
    assert latest["latest_rejection"]["reason"] == "sniffed media URL must be a playable media URL or manifest"
    assert latest["latest_rejection"]["content_type"] == "text/html"
    assert latest["latest_rejection"]["request_headers"]["Cookie"] == "<redacted>"
    assert "secret" not in str(latest)


def test_api_latest_rejected_sniff_matches_page_without_query(tmp_path):
    response = client.post(
        "/sniffed-media/plan",
        json={
            "url": "https://example.com/watch?token=secret",
            "page_url": "https://example.com/watch?reload=1",
            "content_type": "text/html",
            "resource_type": "main_frame",
            "status_code": 200,
            "output_dir": str(tmp_path / "downloads"),
        },
    )

    assert response.status_code == 400
    latest = client.get("/sniffed-media/latest", params={"page_url": "https://example.com/watch?from=3"}).json()

    assert latest["status"] == "not_found"
    assert latest["rejection_count"] == 1
    assert latest["rejection_page_match"] == "normalized"
    assert latest["latest_rejection"]["content_type"] == "text/html"


def test_api_manifest_probe_returns_summary_without_persisting_playlist_text(tmp_path):
    created = client.post(
        "/sniffed-media/plan",
        json={
            "url": "https://cdn.example.com/master.m3u8?token=secret",
            "page_url": "https://example.com/watch",
            "capture_session_id": "v11-hls",
            "tab_id": 4,
            "media_type": "HLS",
            "category": "stream",
            "content_type": "application/vnd.apple.mpegurl",
            "output_dir": str(tmp_path / "downloads"),
        },
    ).json()
    playlist = "#EXTM3U\n#EXTINF:90,\nsegment.ts?token=playlist-secret\n#EXT-X-ENDLIST\n"

    response = client.post(f"/sniffed-media/{created['capture_id']}/manifest-probe", json={"playlist_text": playlist})

    assert response.status_code == 200
    payload = response.json()
    assert payload["will_download"] is False
    assert payload["manifest_probe"]["playlist_kind"] == "media"
    assert "playlist-secret" not in str(payload)
    latest = client.get("/sniffed-media/latest", params={"page_url": "https://example.com/watch", "capture_session_id": "v11-hls"}).json()
    assert latest["capture"]["manifest_probe"]["risk"] == "likely_complete"
    assert "playlist-secret" not in str(latest)


def test_api_latest_capture_groups_by_tab_and_rejects_segment_only(tmp_path):
    first = client.post(
        "/sniffed-media/plan",
        json={
            "url": "https://cdn.example.com/full.m3u8",
            "page_url": "https://example.com/watch",
            "capture_session_id": "v11-group",
            "tab_id": 1,
            "media_type": "HLS",
            "category": "stream",
            "output_dir": str(tmp_path / "tab-1"),
        },
    ).json()
    client.post(f"/sniffed-media/{first['capture_id']}/manifest-probe", json={"playlist_text": "#EXTM3U\n#EXTINF:90,\nfull.ts\n#EXT-X-ENDLIST\n"})
    client.post(
        "/sniffed-media/plan",
        json={
            "url": "https://cdn.example.com/segment.m4s",
            "page_url": "https://example.com/watch",
            "capture_session_id": "v11-group",
            "tab_id": 2,
            "media_type": "MP4",
            "category": "files",
            "output_dir": str(tmp_path / "tab-2"),
        },
    )

    latest = client.get("/sniffed-media/latest", params={"page_url": "https://example.com/watch", "capture_session_id": "v11-group"}).json()

    assert latest["status"] == "not_found"
    assert latest["capture_count"] == 2
    assert latest["group_capture_count"] == 1
    assert latest["candidates"][0]["selection"]["reason"] == "segment_only"

def test_api_latest_capture_prefers_complete_hls_over_master_direct_range_and_segments(tmp_path):
    common = {
        "page_url": "https://example.com/watch?from=v11",
        "capture_session_id": "v11-combined-candidates",
        "tab_id": 9,
        "output_dir": str(tmp_path / "downloads"),
    }

    master = client.post(
        "/sniffed-media/plan",
        json={
            **common,
            "url": "https://cdn.example.com/master.m3u8",
            "media_type": "HLS",
            "category": "stream",
        },
    ).json()
    client.post(
        f"/sniffed-media/{master['capture_id']}/manifest-probe",
        json={
            "playlist_text": "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=800000\nlow.m3u8\n#EXT-X-STREAM-INF:BANDWIDTH=2500000\nhigh.m3u8\n"
        },
    )
    complete = client.post(
        "/sniffed-media/plan",
        json={
            **common,
            "url": "https://cdn.example.com/full.m3u8",
            "media_type": "HLS",
            "category": "stream",
        },
    ).json()
    client.post(
        f"/sniffed-media/{complete['capture_id']}/manifest-probe",
        json={"playlist_text": "#EXTM3U\n#EXTINF:90,\nfull.ts\n#EXT-X-ENDLIST\n"},
    )
    client.post(
        "/sniffed-media/plan",
        json={**common, "url": "https://cdn.example.com/full.mp4", "media_type": "MP4", "category": "files"},
    )
    client.post(
        "/sniffed-media/plan",
        json={
            **common,
            "url": "https://cdn.example.com/range.mp4",
            "media_type": "MP4",
            "category": "files",
            "content_range": "bytes 0-999/1000000",
        },
    )
    client.post(
        "/sniffed-media/plan",
        json={**common, "url": "https://cdn.example.com/segment.ts", "media_type": "TS", "category": "files"},
    )

    latest = client.get(
        "/sniffed-media/latest",
        params={"page_url": "https://example.com/watch?reload=1", "capture_session_id": common["capture_session_id"]},
    ).json()

    assert latest["status"] == "ok"
    assert latest["capture_page_match"] == "normalized"
    assert latest["capture_count"] == 5
    assert latest["group_capture_count"] == 5
    assert latest["capture"]["url"].endswith("/full.m3u8")
    selection_by_url = {candidate["url"]: candidate["selection"]["reason"] for candidate in latest["candidates"]}
    assert selection_by_url["https://cdn.example.com/full.m3u8"] == "complete_hls_media"
    assert selection_by_url["https://cdn.example.com/master.m3u8"] == "hls_master"
    assert selection_by_url["https://cdn.example.com/full.mp4"] == "direct_or_unprobed"
    assert selection_by_url["https://cdn.example.com/range.mp4"] == "partial_range"
    assert selection_by_url["https://cdn.example.com/segment.ts"] == "segment_only"


def test_api_prefers_complete_douyin_main_video_over_effect_asset(tmp_path):
    common = {
        "page_url": "https://www.douyin.com/video/1234567890",
        "capture_session_id": "douyin-main-vs-effect",
        "tab_id": 88,
        "media_type": "MP4",
        "category": "files",
        "content_type": "video/mp4",
        "output_dir": str(tmp_path / "downloads"),
    }
    client.post(
        "/sniffed-media/plan",
        json={
            **common,
            "url": "https://lf3-effectcdn-tos.byteeffecttos.com/obj/ies.fe.effect/example.mp4",
            "content_length": 226_971,
        },
    )
    client.post(
        "/sniffed-media/plan",
        json={
            **common,
            "url": "https://v26-web.douyinvod.com/video/main.mp4",
            "content_length": 5_334_577,
            "content_range": "bytes 0-5334576/5334577",
            "response_headers": {"content-type": "video/mp4", "content-range": "bytes 0-5334576/5334577"},
        },
    )

    latest = client.get(
        "/sniffed-media/latest",
        params={"page_url": common["page_url"], "capture_session_id": common["capture_session_id"]},
    ).json()

    assert latest["capture"]["url"] == "https://v26-web.douyinvod.com/video/main.mp4"
    assert latest["capture"]["completeness"]["range_kind"] == "complete"
    assert latest["capture"]["completeness"]["risk"] == "likely_complete"
    selection_by_url = {candidate["url"]: candidate["selection"]["reason"] for candidate in latest["candidates"]}
    assert selection_by_url["https://v26-web.douyinvod.com/video/main.mp4"] == "complete_direct_media"
    assert selection_by_url["https://lf3-effectcdn-tos.byteeffecttos.com/obj/ies.fe.effect/example.mp4"] == "auxiliary_asset"

def test_api_manifest_probe_follows_selected_variant_without_returning_urls(tmp_path):
    created = client.post(
        "/sniffed-media/plan",
        json={
            "url": "https://cdn.example.com/master.m3u8?token=capture-secret",
            "page_url": "https://example.com/watch",
            "capture_session_id": "v12-deep-hls",
            "tab_id": 12,
            "media_type": "HLS",
            "category": "stream",
            "output_dir": str(tmp_path / "downloads"),
        },
    ).json()
    master_text = (
        "#EXTM3U\n"
        "#EXT-X-STREAM-INF:BANDWIDTH=800000,RESOLUTION=640x360\nlow.m3u8\n"
        "#EXT-X-STREAM-INF:BANDWIDTH=2500000,RESOLUTION=1920x1080\nhigh.m3u8?token=variant-secret\n"
    )

    master_response = client.post(
        f"/sniffed-media/{created['capture_id']}/manifest-probe",
        json={"playlist_text": master_text, "probe_depth": 0},
    )

    assert master_response.status_code == 200
    master_payload = master_response.json()
    assert master_payload["follow_up"]["variant_index"] == 1
    assert master_payload["follow_up"]["same_origin_only"] is True
    assert master_payload["selected_variant"]["width"] == 1920
    assert "variant-secret" not in str(master_payload)
    assert "high.m3u8" not in str(master_payload)

    media_response = client.post(
        f"/sniffed-media/{created['capture_id']}/manifest-probe",
        json={"playlist_text": "#EXTM3U\n#EXTINF:90,\nsegment.ts?token=segment-secret\n#EXT-X-ENDLIST\n", "probe_depth": 1},
    )

    assert media_response.status_code == 200
    payload = media_response.json()
    assert payload["manifest_probe"]["playlist_kind"] == "media"
    assert payload["manifest_probe"]["has_endlist"] is True
    assert payload["manifest_probe"]["duration_seconds"] == 90
    assert payload["manifest_probe"]["master_playlist"]["playlist_kind"] == "master"
    assert payload["manifest_probe"]["follow_up_status"] == "completed"
    assert payload["manifest_probe"]["probe_request_count"] == 2
    assert "segment-secret" not in str(payload)

    over_budget = client.post(
        f"/sniffed-media/{created['capture_id']}/manifest-probe",
        json={"probe_status": "fetch_failed", "probe_depth": 1},
    )
    assert over_budget.status_code == 400


def test_api_manifest_probe_rejects_variant_without_master(tmp_path):
    created = client.post(
        "/sniffed-media/plan",
        json={
            "url": "https://cdn.example.com/media.m3u8",
            "page_url": "https://example.com/watch",
            "capture_session_id": "v12-invalid-depth",
            "tab_id": 13,
            "media_type": "HLS",
            "category": "stream",
            "output_dir": str(tmp_path / "downloads"),
        },
    ).json()

    response = client.post(
        f"/sniffed-media/{created['capture_id']}/manifest-probe",
        json={"playlist_text": "#EXTM3U\n#EXTINF:90,\nsegment.ts\n", "probe_depth": 1},
    )

    assert response.status_code == 400


def test_api_manifest_probe_auto_identifies_audio_only_hls_master(tmp_path):
    created = client.post(
        "/sniffed-media/plan",
        json={
            "url": "https://cdn.example.com/radio/master.m3u8",
            "page_url": "https://example.com/radio",
            "capture_session_id": "audio-hls-master",
            "tab_id": 21,
            "media_type": "HLS",
            "media_kind": "adaptive",
            "category": "stream",
            "content_type": "application/vnd.apple.mpegurl",
            "output_dir": str(tmp_path / "downloads"),
        },
    ).json()
    master = (
        "#EXTM3U\n"
        "#EXT-X-STREAM-INF:BANDWIDTH=96000,CODECS=\"mp4a.40.2\"\nlow.m3u8\n"
        "#EXT-X-STREAM-INF:BANDWIDTH=256000,CODECS=\"mp4a.40.2\"\nhigh.m3u8\n"
    )

    payload = client.post(
        f"/sniffed-media/{created['capture_id']}/manifest-probe",
        json={"playlist_text": master, "probe_depth": 0},
    ).json()
    latest = client.get(
        "/sniffed-media/latest",
        params={"page_url": "https://example.com/radio", "capture_session_id": "audio-hls-master"},
    ).json()

    assert payload["follow_up"]["variant_index"] == 1
    assert "纯音频" in payload["follow_up"]["reason"]
    assert payload["manifest_probe"]["playlist_media_kind"] == "audio"
    assert latest["capture"]["media_kind"] == "audio"

def test_api_manifest_probe_records_cross_origin_waiting_without_variant_url(tmp_path):
    created = client.post(
        "/sniffed-media/plan",
        json={
            "url": "https://cdn.example.com/master.m3u8",
            "page_url": "https://example.com/watch",
            "capture_session_id": "v12-cross-origin",
            "tab_id": 14,
            "media_type": "HLS",
            "category": "stream",
            "output_dir": str(tmp_path / "downloads"),
        },
    ).json()
    master = client.post(
        f"/sniffed-media/{created['capture_id']}/manifest-probe",
        json={
            "playlist_text": "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=2500000,RESOLUTION=1920x1080\nhttps://other.example/high.m3u8?token=secret\n",
            "probe_depth": 0,
        },
    )
    assert master.status_code == 200

    waiting = client.post(
        f"/sniffed-media/{created['capture_id']}/manifest-probe",
        json={"probe_status": "cross_origin_waiting", "error_code": "cross_origin_waiting", "probe_depth": 1},
    )

    assert waiting.status_code == 200
    payload = waiting.json()
    assert payload["manifest_probe"]["follow_up_status"] == "cross_origin_waiting"
    assert "等待播放器自然请求" in payload["next_action"]
    assert "other.example" not in str(payload)
    assert "secret" not in str(payload)
