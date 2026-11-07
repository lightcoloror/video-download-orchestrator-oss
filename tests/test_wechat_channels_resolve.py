"""微信视频号 P2 显式执行与 MediaGo 入队测试。

覆盖：helper 解析编排（not_configured/resolved/review_required/unavailable/rejected）、
媒体地址 SSRF 与白名单校验、非敏感结果记录（manifest/report/review-checklist/TaskStore）、
确认后 MediaGo 下载（默认立即开始，可手动只入队）、API 与 CLI 入口。
所有测试不访问真实视频、不启动代理、不安装证书、不执行下载。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from video_orchestrator.api import app
from video_orchestrator.runtime.task_store import TaskStore
from video_orchestrator.runtime.wechat_channels_resolve import record_wechat_channels_resolution
from video_orchestrator.runtime.wechat_channels_resolve import resolve_and_import_wechat_channels
from video_orchestrator.runtime.wechat_channels_resolve import resolve_wechat_channels_share
from video_orchestrator.runtime.wechat_channels_resolve import _select_writable_output_dir


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "wechat_channels"
client = TestClient(app)

SHARE_URL = "https://weixin.qq.com/sph/Ali0QjN99U"
CONFIGURED = {
    "helper": {
        "http_base": "http://127.0.0.1:17820",
        "status_endpoint": "/healthy",
        "resolve_endpoint": "/api/parse",
        "timeout_seconds": 2,
        "api_key_env": "",
        "media_host_suffixes": ["channels.weixin.qq.com", "finder.video.qq.com", "qpic.cn", "wxapp.tc.qq.com"],
    }
}


@pytest.fixture()
def resolve_fixtures() -> dict:
    return json.loads((FIXTURE_DIR / "fixtures.json").read_text(encoding="utf-8"))


@pytest.fixture()
def configured(monkeypatch):
    monkeypatch.setattr(
        "video_orchestrator.runtime.wechat_channels_resolve.wechat_channels_config",
        lambda: CONFIGURED,
    )


class FakeResponse:
    def __init__(self, payload=None, status_code=200, text=""):
        self._payload = payload
        self.status_code = status_code
        self._text = text

    def json(self):
        if self._payload is not None:
            return self._payload
        raise ValueError(self._text or "no json")

    @property
    def text(self):
        return self._text


def _fake_post(payload):
    def fake_post(*args, **kwargs):
        return FakeResponse(payload=payload)

    return fake_post


def _public_resolver(hostname, port=None):
    """Keep synthetic Tencent hosts independent from host DNS or proxy DNS."""
    return ["93.184.216.34"]


# ---- 解析编排 ----

def test_resolve_not_configured(monkeypatch):
    monkeypatch.setattr(
        "video_orchestrator.runtime.wechat_channels_resolve.wechat_channels_config",
        lambda: {"helper": {"http_base": "", "resolve_endpoint": "/api/parse", "timeout_seconds": 2, "api_key_env": ""}},
    )

    result = resolve_wechat_channels_share(SHARE_URL)

    assert result["status"] == "not_configured"
    assert result["will_download"] is False
    assert result["credentials_accessed"] is False
    assert "解析" in result["next_action"]


def test_resolve_success(configured, resolve_fixtures):
    case = resolve_fixtures["resolve_responses"][0]
    result = resolve_wechat_channels_share(
        SHARE_URL,
        request_post=_fake_post(case["response"]),
        resolver=_public_resolver,
    )

    assert result["status"] == "resolved"
    assert result["title"] == "合成示例视频标题"
    assert result["quality"] == "1080p"
    assert result["duration_seconds"] == 123
    assert result["duration_hint"] == "2分3秒"
    # 完整媒体地址只在内存返回 dict 中
    assert result["media_url"].startswith("https://finder.video.qq.com/")
    assert result["redacted_media_url"] != result["media_url"]
    assert "PLACEHOLDER_SIGN" not in result["redacted_media_url"]
    assert result["will_download"] is False


def test_resolve_default_transport_ignores_environment_proxy(configured, resolve_fixtures, monkeypatch):
    case = resolve_fixtures["resolve_responses"][0]
    observed: dict[str, object] = {}

    class FakeSession:
        trust_env = True

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, url, **kwargs):
            observed["trust_env"] = self.trust_env
            observed["url"] = url
            return FakeResponse(payload=case["response"])

    monkeypatch.setattr("video_orchestrator.runtime.wechat_channels_resolve.requests.Session", FakeSession)

    result = resolve_wechat_channels_share(SHARE_URL, resolver=_public_resolver)

    assert result["status"] == "resolved"
    assert observed["trust_env"] is False
    assert str(observed["url"]).startswith("http://127.0.0.1:")


def test_resolve_auth_expired_is_review_required(configured, resolve_fixtures):
    case = resolve_fixtures["resolve_responses"][1]
    result = resolve_wechat_channels_share(SHARE_URL, request_post=_fake_post(case["response"]))

    assert result["status"] == "review_required"
    assert result["reason"] == "auth_expired"
    assert result["will_download"] is False
    assert "media_url" not in result
    assert "登录" in result["next_action"]


def test_resolve_missing_media_url_is_review_required(configured):
    result = resolve_wechat_channels_share(
        SHARE_URL,
        request_post=_fake_post({"status": "resolved"}),
    )

    assert result["status"] == "review_required"
    assert result["reason"] == "missing_media_url"
    assert result["will_download"] is False


def test_resolve_helper_unreachable_is_unavailable(configured):
    def broken_post(*args, **kwargs):
        raise OSError("connection refused")

    result = resolve_wechat_channels_share(SHARE_URL, request_post=broken_post)

    assert result["status"] == "unavailable"
    assert result["will_download"] is False
    assert result["credentials_accessed"] is False
    assert "不误报成功" in result["next_action"]


def test_resolve_helper_http_error_is_unavailable(configured):
    result = resolve_wechat_channels_share(
        SHARE_URL,
        request_post=lambda *args, **kwargs: FakeResponse(payload={"status": "resolved"}, status_code=503, text="boom"),
    )

    assert result["status"] == "unavailable"
    assert result["reason"] == "helper_http_error"


def test_resolve_preserves_unavailable_contract_from_503(configured):
    result = resolve_wechat_channels_share(
        SHARE_URL,
        request_post=lambda *args, **kwargs: FakeResponse(
            payload={"status": "unavailable", "reason": "network_error", "next_action": "稍后重试"},
            status_code=503,
        ),
    )

    assert result["status"] == "unavailable"
    assert result["reason"] == "network_error"
    assert result["next_action"] == "稍后重试"


def test_resolve_helper_invalid_json_is_unavailable(configured):
    result = resolve_wechat_channels_share(
        SHARE_URL,
        request_post=lambda *args, **kwargs: FakeResponse(status_code=200, text="not json"),
    )

    assert result["status"] == "unavailable"
    assert result["reason"] == "invalid_helper_payload"


# ---- 安全校验 ----

def test_resolve_rejects_non_loopback_helper(monkeypatch):
    monkeypatch.setattr(
        "video_orchestrator.runtime.wechat_channels_resolve.wechat_channels_config",
        lambda: {
            "helper": {
                "http_base": "http://192.168.1.50:17820",
                "resolve_endpoint": "/api/parse",
                "timeout_seconds": 2,
                "api_key_env": "",
            }
        },
    )

    result = resolve_wechat_channels_share(SHARE_URL)

    assert result["status"] == "rejected"
    assert "loopback" in result["next_action"]
    assert result["will_download"] is False


def test_resolve_rejects_localhost_media_url(configured):
    result = resolve_wechat_channels_share(
        SHARE_URL,
        request_post=_fake_post(
            {
                "status": "resolved",
                "media_url": "http://127.0.0.1/evil.mp4",
                "title": "evil",
            }
        ),
    )

    assert result["status"] == "rejected"
    assert result["reason"] == "media_host_not_allowed"
    assert result["will_download"] is False


def test_resolve_rejects_private_media_ip(configured):
    result = resolve_wechat_channels_share(
        SHARE_URL,
        request_post=_fake_post(
            {
                "status": "resolved",
                "media_url": "http://10.0.0.5/video.mp4",
                "title": "private",
            }
        ),
    )

    assert result["status"] == "rejected"
    assert result["reason"] == "media_host_not_allowed"


def test_resolve_rejects_non_tencent_media_host(configured):
    result = resolve_wechat_channels_share(
        SHARE_URL,
        request_post=_fake_post(
            {
                "status": "resolved",
                "media_url": "https://evil.example.com/video.mp4",
                "title": "evil",
            }
        ),
    )

    assert result["status"] == "rejected"
    assert result["reason"] == "media_host_not_allowed"


def test_resolve_rejects_whitelisted_host_resolving_private(configured):
    """白名单域名被解析到非公网地址时也必须拒绝（防 DNS 重绑定/SSRF）。"""

    def private_resolver(hostname, port=None):
        return ["10.0.0.9"]

    result = resolve_wechat_channels_share(
        SHARE_URL,
        request_post=_fake_post(
            {
                "status": "resolved",
                "media_url": "https://finder.video.qq.com/video.mp4",
                "title": "rebind",
            }
        ),
        resolver=private_resolver,
    )

    assert result["status"] == "rejected"
    assert result["reason"] == "unsafe_media_url:non_public_address"


def test_resolve_accepts_configured_media_host_suffix(monkeypatch):
    monkeypatch.setattr(
        "video_orchestrator.runtime.wechat_channels_resolve.wechat_channels_config",
        lambda: {
            "helper": {
                "http_base": "http://127.0.0.1:17820",
                "resolve_endpoint": "/api/parse",
                "timeout_seconds": 2,
                "api_key_env": "",
                "media_host_suffixes": ["cdn.example.com"],
            }
        },
    )

    def public_resolver(hostname, port=None):
        return ["93.184.216.34"]

    result = resolve_wechat_channels_share(
        SHARE_URL,
        request_post=_fake_post(
            {
                "status": "resolved",
                "media_url": "https://cdn.example.com/video.mp4?sign=PLACEHOLDER_SIGN",
                "title": "ok",
            }
        ),
        resolver=public_resolver,
    )

    assert result["status"] == "resolved"
    assert result["media_url"] == "https://cdn.example.com/video.mp4?sign=PLACEHOLDER_SIGN"
    # 脱敏形式不保留签名参数
    assert result["redacted_media_url"] == "https://cdn.example.com/video.mp4"
    assert "PLACEHOLDER_SIGN" not in result["redacted_media_url"]


# ---- 非敏感结果记录 ----

def test_record_writes_only_non_sensitive_artifacts(configured, resolve_fixtures, tmp_path):
    case = resolve_fixtures["resolve_responses"][0]
    resolved = resolve_wechat_channels_share(
        SHARE_URL,
        request_post=_fake_post(case["response"]),
        resolver=_public_resolver,
    )
    store = TaskStore(tmp_path / ".vdo" / "tasks.sqlite")

    record = record_wechat_channels_resolution(SHARE_URL, resolved, output_dir=tmp_path, store=store)

    assert record["status"] == "finished"
    assert record["resolution"]["title"] == "合成示例视频标题"
    assert record["resolution"]["status"] == "resolved"
    assert "media_url" not in record["resolution"]
    task = store.get_task(record["task_id"])
    assert task["status"] == "finished"
    artifacts = store.list_artifacts(record["task_id"])
    assert {item["kind"] for item in artifacts} == {"manifest", "report", "review_checklist"}
    # 完整媒体地址与签名参数绝不落盘
    for artifact in artifacts:
        text = Path(artifact["path"]).read_text(encoding="utf-8")
        assert "PLACEHOLDER_SIGN" not in text
        assert "encfilekey" not in text
    manifest_text = Path(record["artifacts"]["manifest_path"]).read_text(encoding="utf-8")
    manifest = json.loads(manifest_text)
    assert manifest["status"] == "resolved"
    assert manifest["will_download"] is False


def test_record_strips_share_query_from_task_and_artifacts(configured, resolve_fixtures, tmp_path):
    case = resolve_fixtures["resolve_responses"][0]
    signed_share = SHARE_URL + "?poc_token=SHARE_SECRET&unknown_signature=HIDDEN"
    resolved = resolve_wechat_channels_share(
        signed_share,
        request_post=_fake_post(case["response"]),
        resolver=_public_resolver,
    )
    store = TaskStore(tmp_path / ".vdo" / "tasks.sqlite")

    record = record_wechat_channels_resolution(signed_share, resolved, output_dir=tmp_path, store=store)

    task = store.get_task(record["task_id"])
    assert task["url"] == SHARE_URL
    for artifact in store.list_artifacts(record["task_id"]):
        text = Path(artifact["path"]).read_text(encoding="utf-8")
        assert "SHARE_SECRET" not in text
        assert "unknown_signature" not in text


def test_record_review_required_marks_retryable(configured, resolve_fixtures, tmp_path):
    case = resolve_fixtures["resolve_responses"][1]
    resolved = resolve_wechat_channels_share(SHARE_URL, request_post=_fake_post(case["response"]))
    store = TaskStore(tmp_path / ".vdo" / "tasks.sqlite")

    record = record_wechat_channels_resolution(SHARE_URL, resolved, output_dir=tmp_path, store=store)

    assert record["status"] == "retryable"
    assert store.get_task(record["task_id"])["status"] == "retryable"


def test_record_unavailable_marks_failed(configured, tmp_path):
    def broken_post(*args, **kwargs):
        raise OSError("connection refused")

    resolved = resolve_wechat_channels_share(SHARE_URL, request_post=broken_post)
    store = TaskStore(tmp_path / ".vdo" / "tasks.sqlite")

    record = record_wechat_channels_resolution(SHARE_URL, resolved, output_dir=tmp_path, store=store)

    assert record["status"] == "failed"
    assert store.get_task(record["task_id"])["status"] == "failed"


# ---- MediaGo 入队（显式确认后）----

def test_import_resolved_enqueues_to_mediago_with_title(configured, resolve_fixtures, tmp_path):
    case = resolve_fixtures["resolve_responses"][0]
    captured: dict = {}

    def fake_import_post(url, json=None, headers=None, timeout=None):
        captured["url"] = url
        captured["payload"] = json
        return FakeResponse(payload={"ok": True, "echo": json["tasks"][0]})

    result = resolve_and_import_wechat_channels(
        SHARE_URL,
        output_dir=tmp_path,
        db_path=tmp_path / ".vdo" / "tasks.sqlite",
        provider="local-helper",
        request_post=_fake_post(case["response"]),
        import_post=fake_import_post,
        resolver=_public_resolver,
    )

    assert result["status"] == "finished"
    assert result["imported"] is True
    assert result["will_download"] is True
    assert result["explicit_user_action"] is True
    assert captured["url"].endswith("/api/downloads")
    assert captured["payload"]["startDownload"] is True
    tasks = captured["payload"]["tasks"]
    assert len(tasks) == 1
    # 标题覆盖为解析元数据；任务 URL 是媒体地址而非分享链接
    assert tasks[0]["name"] == "合成示例视频标题"
    assert tasks[0]["url"].startswith("https://finder.video.qq.com/")
    assert "Ali0QjN99U" not in tasks[0]["url"]
    assert "PLACEHOLDER_SIGN" not in str(result)
    assert "encfilekey" not in str(result)
    assert (
        result["media_import"]["payload"]["tasks"][0]["url"]
        == "https://finder.video.qq.com/251/20302/stodownload"
    )
    assert result["media_import"]["deeplink"] is None
    assert result["media_import"]["response"] == {"received": True}


def test_import_queue_only_flag_is_passed_through(configured, resolve_fixtures, tmp_path):
    case = resolve_fixtures["resolve_responses"][0]
    captured: dict = {}

    def fake_import_post(url, json=None, headers=None, timeout=None):
        captured["payload"] = json
        return FakeResponse(payload={"ok": True})

    result = resolve_and_import_wechat_channels(
        SHARE_URL,
        output_dir=tmp_path,
        db_path=tmp_path / ".vdo" / "tasks.sqlite",
        start_download=False,
        request_post=_fake_post(case["response"]),
        import_post=fake_import_post,
        resolver=_public_resolver,
    )

    assert result["will_download"] is False
    assert captured["payload"]["startDownload"] is False


def test_duplicate_content_reuses_mediago_receipt_and_does_not_reimport(configured, resolve_fixtures, tmp_path):
    case = resolve_fixtures["resolve_responses"][0]
    import_calls = {"count": 0}

    def fake_import_post(url, json=None, headers=None, timeout=None):
        import_calls["count"] += 1
        return FakeResponse(
            payload={
                "data": [
                    {
                        "id": 71,
                        "name": "合成示例视频标题",
                        "type": "direct",
                        "status": "pending",
                        "url": "https://finder.video.qq.com/private.mp4?token=SECRET",
                    }
                ]
            }
        )

    def fake_mediago_get(url, **kwargs):
        return FakeResponse(
            payload={
                "data": {
                    "id": 71,
                    "name": "合成示例视频标题",
                    "type": "direct",
                    "status": "pending",
                    "url": "https://finder.video.qq.com/private.mp4?token=SECRET",
                }
            }
        )

    kwargs = {
        "output_dir": tmp_path,
        "db_path": tmp_path / ".vdo" / "tasks.sqlite",
        "provider": "local-helper",
        "request_post": _fake_post(case["response"]),
        "import_post": fake_import_post,
        "mediago_request_get": fake_mediago_get,
        "resolver": _public_resolver,
    }
    first = resolve_and_import_wechat_channels(SHARE_URL, **kwargs)
    second = resolve_and_import_wechat_channels(SHARE_URL + "?from=timeline", **kwargs)

    assert first["duplicate"] is False
    assert second["duplicate"] is True
    assert second["task_id"] == first["task_id"]
    assert second["imported"] is False
    assert second["will_download"] is False
    assert second["media_import"]["status"] == "already_imported"
    assert second["media_import"]["reconciliation"]["tasks"][0]["download_status"] == "pending"
    assert import_calls["count"] == 1
    receipt_path = Path(first["artifacts"]["mediago_receipt_path"])
    assert receipt_path.exists()
    assert "SECRET" not in receipt_path.read_text(encoding="utf-8")


def test_force_reimport_creates_another_mediago_task(configured, resolve_fixtures, tmp_path):
    case = resolve_fixtures["resolve_responses"][0]
    import_calls = {"count": 0}

    def fake_import_post(url, json=None, headers=None, timeout=None):
        import_calls["count"] += 1
        return FakeResponse(payload={"data": [{"id": 80 + import_calls["count"], "status": "pending"}]})

    kwargs = {
        "output_dir": tmp_path,
        "db_path": tmp_path / ".vdo" / "tasks.sqlite",
        "provider": "local-helper",
        "request_post": _fake_post(case["response"]),
        "import_post": fake_import_post,
        "resolver": _public_resolver,
    }
    resolve_and_import_wechat_channels(SHARE_URL, **kwargs)
    second = resolve_and_import_wechat_channels(SHARE_URL, force_reimport=True, **kwargs)

    assert second["duplicate"] is True
    assert second["imported"] is True
    assert import_calls["count"] == 2


def test_import_output_dir_falls_back_to_unified_runtime(monkeypatch, tmp_path):
    requested = tmp_path / "blocked-downloads"
    state_dir = tmp_path / ".runtime" / "video-download-orchestrator"
    monkeypatch.setattr("video_orchestrator.runtime.wechat_channels_resolve.runtime_state_dir", lambda: state_dir)
    monkeypatch.setattr(
        "video_orchestrator.runtime.wechat_channels_resolve._directory_writable",
        lambda path: path.name == "wechat-channels",
    )

    selected, storage = _select_writable_output_dir(requested)

    assert selected == state_dir / "downloads" / "wechat-channels"
    assert storage["fallback_used"] is True
    assert storage["reason"] == "requested_output_dir_not_writable"


def test_import_not_resolved_skips_mediago(configured, resolve_fixtures, tmp_path):
    case = resolve_fixtures["resolve_responses"][1]
    called = {"count": 0}

    def fake_import_post(*args, **kwargs):
        called["count"] += 1
        return FakeResponse(payload={"ok": True})

    result = resolve_and_import_wechat_channels(
        SHARE_URL,
        output_dir=tmp_path,
        db_path=tmp_path / ".vdo" / "tasks.sqlite",
        provider="local-helper",
        request_post=_fake_post(case["response"]),
        import_post=fake_import_post,
    )

    assert result["status"] == "retryable"
    assert result["imported"] is False
    assert called["count"] == 0


# ---- API 端点 ----

def test_api_resolve_endpoint_only_resolves(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "video_orchestrator.api.resolve_wechat_channels_share",
        lambda url: {
            "status": "resolved",
            "share_url": SHARE_URL,
            "title": "标题",
            "media_url": "https://finder.video.qq.com/x.mp4",
            "redacted_media_url": "https://finder.video.qq.com/x.mp4",
            "next_action": "确认后导入 MediaGo。",
        },
    )

    response = client.post(
        "/platform/wechat-channels/resolve",
        json={"url": SHARE_URL, "output_dir": str(tmp_path), "db_path": str(tmp_path / "tasks.sqlite")},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["will_download"] is False
    assert payload["imported"] is False
    assert payload["explicit_user_action"] is True


def test_api_import_endpoint_enqueues(monkeypatch, tmp_path):
    captured: dict = {}

    def fake_import(*args, **kwargs):
        captured["kwargs"] = kwargs
        return {
            "status": "queued",
            "imported": True,
            "will_download": bool(kwargs.get("start_download")),
            "explicit_user_action": True,
            "resolution": {"status": "resolved"},
        }

    monkeypatch.setattr("video_orchestrator.api.resolve_and_import_wechat_channels", fake_import)

    response = client.post(
        "/platform/wechat-channels/import",
        json={"url": SHARE_URL, "output_dir": str(tmp_path), "db_path": str(tmp_path / "tasks.sqlite")},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["imported"] is True
    assert payload["will_download"] is False
    assert captured["kwargs"]["start_download"] is False
    assert captured["kwargs"]["force_reimport"] is False


def test_api_import_endpoint_rejects_non_share_url(tmp_path):
    response = client.post(
        "/platform/wechat-channels/import",
        json={"url": "not a url", "output_dir": str(tmp_path), "db_path": str(tmp_path / "tasks.sqlite")},
    )

    assert response.status_code == 422


# ---- CLI ----

def test_cli_commands_registered():
    from video_orchestrator.cli import app as cli_app

    names = [command.name for command in cli_app.registered_commands]
    assert "wechat-channels-resolve" in names
    assert "wechat-channels-import" in names
    assert "mediago-task-status" in names


def test_cli_wechat_channels_import_not_configured(monkeypatch, tmp_path):
    from typer.testing import CliRunner

    from video_orchestrator.cli import app as cli_app

    monkeypatch.setattr(
        "video_orchestrator.runtime.wechat_channels_resolve.wechat_channels_config",
        lambda: {"helper": {"http_base": "", "resolve_endpoint": "/api/parse", "timeout_seconds": 2, "api_key_env": ""}},
    )

    result = CliRunner().invoke(
        cli_app,
        [
            "wechat-channels-import",
            SHARE_URL,
            "--output-dir",
            str(tmp_path),
            "--db-path",
            str(tmp_path / "tasks.sqlite"),
            "--provider",
            "local-helper",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["resolution"]["status"] == "not_configured"
    assert payload["imported"] is False
    assert payload["will_download"] is False
