"""微信视频号 P1 planning-only 适配测试。

覆盖：站点适配器识别、路由分类、批量清洗（提取/去重/规范化/脱敏）、
本地 helper 状态检查、planning-only 安全失败、MediaGo 交接排除。
所有测试默认不访问真实视频、不启动代理、不安装证书、不执行下载。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from video_orchestrator.api import app
from video_orchestrator.backends.base import get_backend_adapter
from video_orchestrator.runtime.batch_cleaner import clean_batch_input
from video_orchestrator.runtime.batch_cleaner import normalize_batch_url
from video_orchestrator.runtime.mediago import build_mediago_handoff_plan
from video_orchestrator.runtime.redaction import redact_url
from video_orchestrator.runtime.site_adapters import match_site_adapter
from video_orchestrator.runtime.site_adapters import site_adapter_diagnostic
from video_orchestrator.runtime.wechat_channels_helper import wechat_channels_helper_status
from video_orchestrator.router import classify_url
from video_orchestrator.services import plan_download


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "wechat_channels"
client = TestClient(app)


@pytest.fixture()
def share_url_fixtures() -> dict:
    return json.loads((FIXTURE_DIR / "fixtures.json").read_text(encoding="utf-8"))


def _fixture_cases(share_url_fixtures: dict) -> list[dict]:
    return [case for case in share_url_fixtures["cases"] if case["kind"] == "wechat-channels-page"]


# ---- 站点适配器 ----

def test_site_adapter_matches_wechat_channels_share_links(share_url_fixtures):
    for case in _fixture_cases(share_url_fixtures):
        adapter = match_site_adapter(case["raw"])

        assert adapter is not None, case["name"]
        assert adapter.name == "wechat-channels"
        assert adapter.kind == "wechat-channels-page"
        assert adapter.backend == "wechat-channels-helper"
        assert "planning_only" in adapter.capabilities
        assert adapter.fallback_mode == "manual_review"


def test_site_adapter_does_not_match_other_weixin_paths(share_url_fixtures):
    for case in share_url_fixtures["cases"]:
        if case["kind"] == "wechat-channels-page":
            continue
        assert match_site_adapter(case["raw"]) is None, case["name"]


def test_site_adapter_diagnostic_reports_planning_only(share_url_fixtures):
    diagnostic = site_adapter_diagnostic(_fixture_cases(share_url_fixtures)[0]["raw"])

    assert diagnostic["matched"] is True
    assert diagnostic["name"] == "wechat-channels"
    assert diagnostic["kind"] == "wechat-channels-page"
    assert diagnostic["backend"] == "wechat-channels-helper"
    assert diagnostic["fallback_mode"] == "manual_review"
    assert diagnostic["supports_site_fallback"] is False
    assert "planning_only" in diagnostic["capabilities"]


# ---- 路由分类 ----

def test_router_classifies_wechat_channels_as_planning_only(share_url_fixtures):
    for case in _fixture_cases(share_url_fixtures):
        result = classify_url(case["raw"])

        assert result.kind == "wechat-channels-page"
        assert result.preferred_backend == "wechat-channels-helper"


# ---- 批量清洗 ----

def test_clean_batch_input_extracts_and_dedupes_wechat_channels_from_mixed_text():
    raw = """
    视频分享：
    https://weixin.qq.com/sph/Ali0QjN99U?scene=1&poc_token=SECRET_ONE
    再发一次（带不同跟踪参数，应去重）：
    https://weixin.qq.com/sph/Ali0QjN99U?from=timeline&poc_token=SECRET_TWO
    说明文字。另外一条：
    https://weixin.qq.com/sph/Bbb2XyZ000
    """

    payload = clean_batch_input(raw)

    assert payload["item_count"] == 2
    assert [item["url"] for item in payload["items"]] == [
        "https://weixin.qq.com/sph/Ali0QjN99U",
        "https://weixin.qq.com/sph/Bbb2XyZ000",
    ]
    assert all(item["cleaning"] == "wechat_channels_share_link" for item in payload["items"])
    # 敏感查询参数不在结果中出现
    assert "SECRET_ONE" not in str(payload)
    assert "SECRET_TWO" not in str(payload)
    assert "poc_token" not in str(payload)
    # 平台摘要识别为 planning_only
    assert payload["platforms"]["wechat-channels"]["count"] == 2
    assert payload["platforms"]["wechat-channels"]["planning_only"] is True


def test_normalize_wechat_channels_strips_all_query_parameters(share_url_fixtures):
    case = share_url_fixtures["cases"][1]
    normalized, reason = normalize_batch_url(case["raw"])

    assert reason == "wechat_channels_share_link"
    assert normalized == case["canonical"]
    assert "?" not in normalized


def test_normalize_wechat_channels_missing_id_is_rejected(share_url_fixtures):
    case = share_url_fixtures["cases"][5]
    normalized, reason = normalize_batch_url(case["raw"])

    assert normalized is None
    assert reason == "missing_wechat_channels_id"


def test_clean_wechat_channels_url_is_redacted_in_outputs():
    url = "https://weixin.qq.com/sph/Ali0QjN99U?poc_token=SUPERSECRET"

    assert "SUPERSECRET" not in redact_url(url)


# ---- 本地 helper 状态检查（只报告可用性，不读凭证）----

def test_helper_status_not_configured(monkeypatch):
    monkeypatch.setattr(
        "video_orchestrator.runtime.wechat_channels_helper.wechat_channels_config",
        lambda: {"helper": {"http_base": "", "status_endpoint": "/healthy", "timeout_seconds": 2, "api_key_env": ""}},
    )

    payload = wechat_channels_helper_status()

    assert payload["helper_status"] == "not_configured"
    assert payload["credentials_accessed"] is False
    assert payload["will_download"] is False
    assert "解析" in payload["next_action"]


def test_helper_status_available(monkeypatch):
    monkeypatch.setattr(
        "video_orchestrator.runtime.wechat_channels_helper.wechat_channels_config",
        lambda: {"helper": {"http_base": "http://127.0.0.1:17820", "status_endpoint": "/healthy", "timeout_seconds": 2, "api_key_env": ""}},
    )

    class FakeResponse:
        status_code = 200

    def fake_get(*args, **kwargs):
        return FakeResponse()

    payload = wechat_channels_helper_status(request_get=fake_get)

    assert payload["helper_status"] == "available"
    assert payload["credentials_accessed"] is False


def test_helper_status_default_transport_ignores_environment_proxy(monkeypatch):
    monkeypatch.setattr(
        "video_orchestrator.runtime.wechat_channels_helper.wechat_channels_config",
        lambda: {
            "helper": {
                "http_base": "http://127.0.0.1:17820",
                "status_endpoint": "/healthy",
                "timeout_seconds": 2,
                "api_key_env": "",
            }
        },
    )
    observed: dict[str, object] = {}

    class FakeResponse:
        status_code = 200

    class FakeSession:
        trust_env = True

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, url, **kwargs):
            observed["trust_env"] = self.trust_env
            observed["url"] = url
            return FakeResponse()

    monkeypatch.setattr("video_orchestrator.runtime.wechat_channels_helper.requests.Session", FakeSession)

    payload = wechat_channels_helper_status()

    assert payload["helper_status"] == "available"
    assert observed["trust_env"] is False
    assert str(observed["url"]).startswith("http://127.0.0.1:")


def test_helper_status_unavailable(monkeypatch):
    monkeypatch.setattr(
        "video_orchestrator.runtime.wechat_channels_helper.wechat_channels_config",
        lambda: {"helper": {"http_base": "http://127.0.0.1:17820", "status_endpoint": "/healthy", "timeout_seconds": 2, "api_key_env": ""}},
    )

    class FakeResponse:
        status_code = 503

    def fake_get(*args, **kwargs):
        return FakeResponse()

    payload = wechat_channels_helper_status(request_get=fake_get)

    assert payload["helper_status"] == "unavailable"
    assert payload["credentials_accessed"] is False
    assert "不误报成功" in payload["next_action"]


def test_helper_status_connection_error(monkeypatch):
    monkeypatch.setattr(
        "video_orchestrator.runtime.wechat_channels_helper.wechat_channels_config",
        lambda: {"helper": {"http_base": "http://127.0.0.1:17820", "status_endpoint": "/healthy", "timeout_seconds": 2, "api_key_env": ""}},
    )

    def fake_get(*args, **kwargs):
        raise OSError("connection refused")

    payload = wechat_channels_helper_status(request_get=fake_get)

    assert payload["helper_status"] == "unavailable"
    assert payload["credentials_accessed"] is False


def test_helper_status_rejects_remote_base_before_sending_credentials(monkeypatch):
    monkeypatch.setattr(
        "video_orchestrator.runtime.wechat_channels_helper.wechat_channels_config",
        lambda: {
            "helper": {
                "http_base": "https://helper.example.test",
                "status_endpoint": "/healthy",
                "timeout_seconds": 2,
                "api_key_env": "FIXTURE_API_KEY",
            }
        },
    )
    called = {"count": 0}

    def fake_get(*args, **kwargs):
        called["count"] += 1
        raise AssertionError("remote helper must not be contacted")

    payload = wechat_channels_helper_status(request_get=fake_get)

    assert payload["helper_status"] == "rejected"
    assert payload["credentials_accessed"] is False
    assert called["count"] == 0


# ---- planning-only 安全失败 ----

def test_plan_download_for_wechat_channels_fails_safely():
    with pytest.raises(ValueError, match="planning-only"):
        plan_download("https://weixin.qq.com/sph/Ali0QjN99U")


def test_wechat_channels_helper_backend_is_registered_but_does_not_download():
    adapter = get_backend_adapter("wechat-channels-helper")

    assert adapter.spec.name == "wechat-channels-helper"
    assert adapter.spec.command != "python"
    assert adapter.spec.fallbacks == []
    with pytest.raises(ValueError, match="planning-only"):
        adapter.build_command("https://weixin.qq.com/sph/Ali0QjN99U", Path("downloads"))


# ---- MediaGo 交接排除 ----

def test_mediago_handoff_excludes_wechat_channels_from_download_tasks(tmp_path):
    plan = build_mediago_handoff_plan(
        ["https://weixin.qq.com/sph/Ali0QjN99U", "https://example.com/file.mp4"],
        output_dir=tmp_path,
    )

    assert plan["status"] == "planned"
    assert plan["will_download"] is False
    assert plan["task_count"] == 1
    assert plan["payload"]["tasks"][0]["url"] == "https://example.com/file.mp4"
    assert plan["wechat_channels"]["recognized"] == 1
    assert plan["wechat_channels"]["status"] == "review_required"
    assert plan["wechat_channels"]["will_download"] is False
    assert "Ali0QjN99U" in plan["wechat_channels"]["urls"][0]


def test_mediago_handoff_only_wechat_channels_has_no_tasks(tmp_path):
    plan = build_mediago_handoff_plan(["https://weixin.qq.com/sph/Ali0QjN99U"], output_dir=tmp_path)

    assert plan["task_count"] == 0
    assert plan["payload"]["tasks"] == []
    assert plan["wechat_channels"]["recognized"] == 1


# ---- API 端点 ----

def test_api_route_reports_wechat_channels_planning_only():
    response = client.get("/route", params={"url": "https://weixin.qq.com/sph/Ali0QjN99U"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["route"]["kind"] == "wechat-channels-page"
    assert payload["site_adapter"]["name"] == "wechat-channels"
    assert payload["will_download"] is False


def test_api_wechat_channels_helper_status_not_configured(monkeypatch):
    monkeypatch.setattr(
        "video_orchestrator.runtime.wechat_channels_helper.wechat_channels_config",
        lambda: {"helper": {"http_base": "", "status_endpoint": "/healthy", "timeout_seconds": 2, "api_key_env": ""}},
    )

    response = client.get("/platform/wechat-channels/helper-status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["helper_status"] == "not_configured"
    assert payload["credentials_accessed"] is False


def test_api_batch_clean_reports_wechat_channels_platform_summary():
    response = client.post(
        "/batch/clean",
        json={"text": "https://weixin.qq.com/sph/Ali0QjN99U?poc_token=SECRET 重复 https://weixin.qq.com/sph/Ali0QjN99U"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["item_count"] == 1
    assert payload["items"][0]["url"] == "https://weixin.qq.com/sph/Ali0QjN99U"
    assert payload["skipped"][0]["reason"] == "duplicate"
    assert payload["platforms"]["wechat-channels"]["count"] == 1
    assert "SECRET" not in str(payload)
