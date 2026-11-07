import json
from pathlib import Path

from typer.testing import CliRunner

from video_orchestrator.config import app
from video_orchestrator.runtime.config import browser_sniffer_generated_config_path
from video_orchestrator.runtime.config import browser_sniffer_manifest_path
from video_orchestrator.runtime.config import check_config
from video_orchestrator.runtime.config import api_base_url
from video_orchestrator.runtime.config import agent_discovery_json_path
from video_orchestrator.runtime.config import config_path
from video_orchestrator.runtime.config import hard_timeout_seconds
from video_orchestrator.runtime.config import idle_timeout_seconds
from video_orchestrator.runtime.config import load_config
from video_orchestrator.runtime.config import sniffer_plan_endpoint
from video_orchestrator.runtime import sniff_store

runner = CliRunner()


def test_runtime_config_loads_single_project_source():
    config = load_config()

    assert config_path().name == "video_orchestrator.json"
    assert config["web"]["host"] == "127.0.0.1"
    assert isinstance(config["web"]["port"], int)
    assert hard_timeout_seconds() == config["execution"]["hard_timeout_seconds"]
    assert idle_timeout_seconds() == config["execution"]["idle_timeout_seconds"]
    assert api_base_url() == f"{config['web']['scheme']}://{config['web']['host']}:{config['web']['port']}"
    assert sniffer_plan_endpoint() == api_base_url() + config["browser_sniffer"]["plan_path"]


def test_browser_sniffer_generated_config_matches_runtime_config():
    config = load_config()
    generated = (Path(__file__).parents[1] / "integrations" / "browser-sniffer" / "orchestrator-config.js").read_text(
        encoding="utf-8"
    )

    assert f"\"apiBaseUrl\": \"{api_base_url()}\"" in generated
    assert f"\"planPath\": \"{config['browser_sniffer']['plan_path']}\"" in generated
    assert f"\"outputDir\": \"{config['browser_sniffer']['output_dir']}\"" in generated


def test_config_check_passes_for_synced_artifacts():
    payload = check_config()

    assert payload["status"] == "ok"
    assert {check["name"] for check in payload["checks"]} >= {
        "config_file",
        "web_config",
        "browser_sniffer_config",
        "execution_config",
        "site_probe_config",
        "browser_sniffer_generated_config",
        "browser_sniffer_manifest",
        "agent_discovery_json",
    }
    assert all(check["status"] == "ok" for check in payload["checks"])


def test_config_cli_check_outputs_json():
    result = runner.invoke(app, ["check"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "ok"


def test_browser_manifest_uses_port_wildcard_not_configured_port_copy():
    manifest = json.loads(browser_sniffer_manifest_path().read_text(encoding="utf-8"))

    assert "http://127.0.0.1:*/*" in manifest["host_permissions"]
    assert f"http://127.0.0.1:{load_config()['web']['port']}/*" not in manifest["host_permissions"]
    assert manifest["action"]["default_popup"] == "popup.html"


def test_generated_paths_are_under_project_root():
    assert browser_sniffer_generated_config_path().name == "orchestrator-config.js"
    assert browser_sniffer_manifest_path().name == "manifest.json"
    assert agent_discovery_json_path().name == "agent-discovery.json"


def test_agent_discovery_json_matches_runtime_config():
    payload = json.loads(agent_discovery_json_path().read_text(encoding="utf-8"))

    assert payload["service"] == "video-download-orchestrator"
    assert payload["runtime"]["api_base_url"] == api_base_url()
    assert payload["runtime"]["site_probe"]["transport"] == "trusted_local_proxy"
    assert payload["runtime"]["site_probe"]["proxy_configured"] is True
    assert "proxy_url" not in payload["runtime"]["site_probe"]
    assert payload["interfaces"]["http"]["base_url"] == api_base_url()
    assert "GET /external-uis" in payload["interfaces"]["http"]["endpoints"]
    assert "POST /download/execute" in payload["interfaces"]["http"]["endpoints"]
    assert "POST /download/page/execute" in payload["interfaces"]["http"]["endpoints"]
    assert "GET /operator/handoff" in payload["interfaces"]["http"]["endpoints"]
    assert "GET /operator/tasks/{task_id}/handoff" in payload["interfaces"]["http"]["endpoints"]
    assert "GET /operator/tasks/{task_id}/vkp-handoff" in payload["interfaces"]["http"]["endpoints"]
    assert "vdo_plan_download" in payload["interfaces"]["mcp"]["tools"]
    assert "vdo_handoff" in payload["interfaces"]["mcp"]["tools"]
    assert "vdo_task_handoff" in payload["interfaces"]["mcp"]["tools"]
    assert "vdo_task_vkp_handoff" in payload["interfaces"]["mcp"]["tools"]
    assert "download_execute" in payload["interfaces"]["cli"]
    assert payload["safety"]["mcp"] == "planning-only; no real backend execution tools"
    assert "explicit local WebUI/user-triggered" in payload["safety"]["http"]


def test_browser_sniffer_popup_uses_generated_config():
    root = Path(__file__).parents[1] / "integrations" / "browser-sniffer"
    popup = (root / "popup.html").read_text(encoding="utf-8")
    script = (root / "popup.js").read_text(encoding="utf-8")

    assert "popup.js" in popup
    assert "latestStatus" in popup
    assert "copyCommand" in popup
    assert "operatorStatus" in popup
    assert "retryableTasks" in popup
    assert "handoffSummary" in popup
    assert "copyHandoffCommand" in popup
    assert "视频下载助手" in popup
    assert "调试信息" in popup
    assert "snifferPlanEndpoint" in script
    assert "ORCHESTRATOR_CONFIG.apiBaseUrl" in script
    assert "/sniffed-media/latest" in script
    assert "/operator/status" in script
    assert "/operator/tasks/retryable" in script
    assert "/operator/handoff" in script
    assert "已接受捕获" in script
    assert "建议工具" in script
    assert "可重试任务" in script
    assert "captureSession" in script
    assert "download-page" in script






def test_sniff_store_defaults_to_runtime_state_dir(tmp_path, monkeypatch):
    monkeypatch.delenv("VDO_SNIFF_STORE_DIR", raising=False)
    monkeypatch.setattr(sniff_store, "runtime_state_dir", lambda: tmp_path)

    assert sniff_store._resolved_base_dir(None) == tmp_path / "sniff-store"


def test_generated_browser_media_protocols_match_runtime_source():
    root = Path(__file__).parents[1]
    supported = (root / "integrations" / "browser-sniffer" / "supported.js").read_text(encoding="utf-8")
    config = (root / "integrations" / "browser-sniffer" / "orchestrator-config.js").read_text(encoding="utf-8")

    assert "Generated by scripts/sync_runtime_config.py from runtime/media_protocols.py" in supported
    assert '"type": "HLS"' in supported
    assert '"mediaKind": "audio"' in supported
    assert '"contentTypePrefixes"' in supported
    assert "snifferManifestProbeEndpoint" in config

def test_browser_sniffer_v12_deep_probe_is_bounded_and_cleans_request_state():
    root = Path(__file__).parents[1] / "integrations" / "browser-sniffer"
    background = (root / "background.js").read_text(encoding="utf-8")
    helper = (root / "hls-probe.mjs").read_text(encoding="utf-8")

    assert "HLS_PROBE_MAX_REQUESTS = 2" in background
    assert "HLS_PROBE_TOTAL_BYTES = 1024 * 1024" in background
    assert "onBeforeRedirect" in background
    assert "onErrorOccurred" in background
    assert "REQUEST_TTL_MS" in background
    assert "cross_origin_waiting" in background
    assert "variantUriAtIndex" in helper
    assert "resolved.origin !== master.origin" in helper
    assert "storageSafeUrl" in background
    assert "dedupe_key: payload.dedupe_key" not in background

def test_site_probe_proxy_address_has_one_project_config_source():
    root = Path(__file__).parents[1]
    config = load_config()
    script = (root / "scripts" / "sync_runtime_config.py").read_text(encoding="utf-8")
    discovery = (root / "integrations" / "agent-discovery.json").read_text(encoding="utf-8")

    assert config["network"]["site_probe"]["proxy_url"].startswith("http://127.0.0.1:")
    assert "10808" not in script
    assert "10808" not in discovery
